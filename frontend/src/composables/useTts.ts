import { computed, ref, watch, type ComputedRef, type Ref } from "vue";

import type { LocalSettings } from "./useLocalSettings";

/**
 * 口型信号。
 *
 * 故意用可变对象而不是 ref：包络是 60fps 的数据，塞进响应式系统只会让上层
 * 每帧重渲染一次，而真正读它的地方是渲染组件自己的 requestAnimationFrame 循环。
 */
export interface MouthSignal {
    /** 0~1 的张嘴程度 */
    open: number;
}

export interface UseTtsResult {
    enabled: Ref<boolean>;
    isPlaying: Ref<boolean>;
    queueLength: ComputedRef<number>;
    mouth: MouthSignal;
    enqueue: (base64Audio: string, format?: string) => void;
    stop: () => void;
    toggle: () => void;
}

interface QueuedAudio {
    base64: string;
    /** tts_audio_chunk 里带的音频格式（mp3 / wav / pcm …） */
    format: string;
}

// 后端下发的 format 是自由的（TtsAdapter 的注释里就写了 mp3 / wav / pcm），
// 之前这里写死 audio/mp3，换提供方就会静默放不出来
const MIME_BY_FORMAT: Record<string, string> = {
    mp3: "audio/mpeg",
    wav: "audio/wav",
    ogg: "audio/ogg",
    pcm: "audio/pcm",
};

/** 口型包络的采样率。60Hz 够用了，再密只是在给静音段落算没用的 RMS */
const ENVELOPE_FPS = 60;

/** 播放提前量（秒）：留一点余量，免得第一帧就被浏览器掐掉开头 */
const START_DELAY = 0.02;

function base64ToArrayBuffer(base64: string): ArrayBuffer {
    const binary = atob(base64);
    const buffer = new ArrayBuffer(binary.length);
    const view = new Uint8Array(buffer);
    for (let i = 0; i < binary.length; i += 1) {
        view[i] = binary.charCodeAt(i);
    }
    return buffer;
}

/**
 * 从波形算张嘴包络。
 *
 * 按 60Hz 求 RMS，再用 95 分位归一化 —— 用最大值归一化的话，一句里有个爆音
 * 就会把其余的嘴部动作压扁到看不见。最后开个 0.6 次方，让轻声也动得起来。
 */
function computeEnvelope(buffer: AudioBuffer): Float32Array {
    const samples = buffer.getChannelData(0);
    const frames = Math.max(1, Math.ceil(buffer.duration * ENVELOPE_FPS));
    const windowSize = Math.max(1, Math.floor(samples.length / frames));

    const raw = new Float32Array(frames);
    for (let frame = 0; frame < frames; frame += 1) {
        const start = frame * windowSize;
        const end = Math.min(samples.length, start + windowSize);
        let sum = 0;
        for (let i = start; i < end; i += 1) {
            sum += samples[i] * samples[i];
        }
        raw[frame] = Math.sqrt(sum / Math.max(1, end - start));
    }

    const sorted = Float32Array.from(raw).sort();
    const reference = sorted[Math.floor(sorted.length * 0.95)] || 1e-4;
    const scale = 1 / Math.max(reference * 0.85, 1e-4);

    const envelope = new Float32Array(frames);
    for (let frame = 0; frame < frames; frame += 1) {
        const normalized = Math.min(1, raw[frame] * scale);
        envelope[frame] = normalized ** 0.6;
    }
    return envelope;
}

export function useTts(settings: Ref<LocalSettings>): UseTtsResult {
    const isPlaying = ref(false);
    const queue = ref<QueuedAudio[]>([]);
    const mouth: MouthSignal = { open: 0 };

    const queueLength = computed(() => queue.value.length);
    const enabled = computed(() => settings.value.ttsEnabled);

    // 当前正在播放的 Audio 对象（Web Audio 解不开时的退路）
    let currentAudio: HTMLAudioElement | null = null;

    // Web Audio 播放路径：解码一次拿到波形，既能算包络也能精确知道播到哪了
    let audioContext: AudioContext | null = null;
    let currentSource: AudioBufferSourceNode | null = null;
    let currentGain: GainNode | null = null;
    let activeEnvelope: Float32Array | null = null;
    let activeStartedAt = 0;
    let mouthFrame = 0;

    // 当前播放循环的 token，stop 时自增，旧的循环会自行退出
    let currentToken = 0;

    // 当前 playOne 的 resolve，用于 stop 时主动释放
    let currentResolve: (() => void) | null = null;

    function getAudioContext(): AudioContext | null {
        if (audioContext) {
            return audioContext;
        }
        try {
            audioContext = new AudioContext();
        } catch (error) {
            console.warn("[tts] Web Audio 起不来，退回 <audio> 播放：", error);
            return null;
        }
        return audioContext;
    }

    /** 把嘴部动作的采样循环跑起来：直接从包络里取当前时刻的值 */
    function startMouthPump(): void {
        if (mouthFrame !== 0) {
            return;
        }
        const pump = () => {
            mouthFrame = window.requestAnimationFrame(pump);
            if (!activeEnvelope || !audioContext) {
                mouth.open = 0;
                return;
            }
            const index = Math.floor((audioContext.currentTime - activeStartedAt) * ENVELOPE_FPS);
            const target = index >= 0 && index < activeEnvelope.length ? activeEnvelope[index] : 0;
            // 张得快、收得慢，看起来才像在说话
            const factor = target > mouth.open ? 0.55 : 0.22;
            mouth.open += (target - mouth.open) * factor;
        };
        mouthFrame = window.requestAnimationFrame(pump);
    }

    function stopMouthPump(): void {
        if (mouthFrame !== 0) {
            window.cancelAnimationFrame(mouthFrame);
            mouthFrame = 0;
        }
        activeEnvelope = null;
        mouth.open = 0;
    }

    /** 退路：Web Audio 不可用或这段音频解不开时，照旧用 <audio>，只是口型不动 */
    function playWithAudioElement(item: QueuedAudio, token: number): Promise<void> {
        return new Promise((resolve) => {
            const mime = MIME_BY_FORMAT[item.format?.toLowerCase()] ?? "audio/mpeg";
            const audio = new Audio(`data:${mime};base64,${item.base64}`);
            audio.volume = settings.value.ttsVolume;
            currentAudio = audio;

            const finish = () => {
                if (token === currentToken) {
                    currentAudio = null;
                    currentResolve = null;
                }
                resolve();
            };

            currentResolve = finish;

            audio.onended = finish;
            audio.onerror = finish;

            audio.play().catch((err) => {
                console.warn("[tts] 播放失败：", err);
                finish();
            });
        });
    }

    async function playOne(item: QueuedAudio, token: number): Promise<void> {
        const context = getAudioContext();
        if (!context) {
            await playWithAudioElement(item, token);
            return;
        }

        let buffer: AudioBuffer;
        try {
            // 不 await resume：没有用户手势时它一直是 pending，await 下去整条队列就卡死了
            if (context.state !== "running") {
                void context.resume();
            }
            buffer = await context.decodeAudioData(base64ToArrayBuffer(item.base64));
        } catch (error) {
            console.warn("[tts] 这段音频解不开（pcm 之类），退回 <audio>：", error);
            await playWithAudioElement(item, token);
            return;
        }

        const source = context.createBufferSource();
        const gain = context.createGain();
        source.buffer = buffer;
        gain.gain.value = settings.value.ttsVolume;
        source.connect(gain);
        gain.connect(context.destination);

        activeEnvelope = computeEnvelope(buffer);
        activeStartedAt = context.currentTime + START_DELAY;
        startMouthPump();

        await new Promise<void>((resolve) => {
            const finish = () => {
                if (token === currentToken) {
                    currentSource = null;
                    currentGain = null;
                    currentResolve = null;
                    activeEnvelope = null;
                }
                resolve();
            };

            currentSource = source;
            currentGain = gain;
            currentResolve = finish;
            source.onended = finish;

            try {
                source.start(activeStartedAt);
            } catch (error) {
                console.warn("[tts] 起播失败：", error);
                finish();
            }
        });
    }

    async function playLoop() {
        const token = currentToken;
        isPlaying.value = true;

        while (queue.value.length > 0 && token === currentToken) {
            const item = queue.value.shift()!;
            if (!item?.base64) continue;
            await playOne(item, token);
        }

        if (token === currentToken) {
            isPlaying.value = false;
            stopMouthPump();
        }
    }

    function enqueue(base64Audio: string, format = "mp3") {
        if (!settings.value.ttsEnabled) return;
        if (!base64Audio) return;

        queue.value.push({ base64: base64Audio, format });

        if (!isPlaying.value) {
            void playLoop();
        }
    }

    function stop() {
        currentToken += 1;
        queue.value = [];

        if (currentSource) {
            try {
                currentSource.stop();
            } catch {
                // 已经播完了，stop 会抛，忽略
            }
            currentSource = null;
        }
        currentGain = null;

        if (currentAudio) {
            try {
                currentAudio.pause();
                currentAudio.src = "";
            } catch {
                // 忽略
            }
            currentAudio = null;
        }

        if (currentResolve) {
            currentResolve();
            currentResolve = null;
        }

        // 打断时口型立刻归零，不然嘴会僵在半张
        stopMouthPump();
        isPlaying.value = false;
    }

    function toggle() {
        settings.value = {
            ...settings.value,
            ttsEnabled: !settings.value.ttsEnabled,
        };
        if (!settings.value.ttsEnabled) {
            stop();
        }
    }

    // 播放中调整音量，实时生效
    watch(
        () => settings.value.ttsVolume,
        (v) => {
            if (currentAudio) {
                currentAudio.volume = v;
            }
            if (currentGain) {
                currentGain.gain.value = v;
            }
        },
    );

    // 开启语音的这次点击是仅有的「用户手势」，顺手把 AudioContext 建起来唤醒。
    // 留到第一次说话再建的话，Chromium 会因为缺手势把它挂起一秒上下，第一句会慢半拍。
    watch(
        () => settings.value.ttsEnabled,
        (on) => {
            if (!on) {
                return;
            }
            const context = getAudioContext();
            if (context && context.state !== "running") {
                void context.resume();
            }
        },
    );

    return {
        enabled,
        isPlaying,
        queueLength,
        mouth,
        enqueue,
        stop,
        toggle,
    };
}
