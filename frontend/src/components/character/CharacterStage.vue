<template>
    <div ref="containerRef" class="character-stage">
        <p v-if="note" class="stage-note" :data-tone="state">{{ note }}</p>
    </div>
</template>

<script setup lang="ts">
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import {
    AmbientLight,
    Box3,
    Clock,
    DirectionalLight,
    HemisphereLight,
    MathUtils,
    PerspectiveCamera,
    Scene,
    SRGBColorSpace,
    Vector3,
    WebGLRenderer,
} from "three";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { onBeforeUnmount, onMounted, ref } from "vue";

import type { MouthSignal } from "@/composables/useTts";
import type { Emotion } from "@/types/protocol";

/**
 * 模型放 public/model/character/ 下，用相对路径引用。
 * 渲染层的 base 是 "./"，写死 "/model/..." 在打包后会指到盘符根目录。
 */
const MODEL_URL = "model/character/xiadie.vrm";

/** 取景留白：1.0 刚好贴边，越大离角色越远 */
const FIT_MARGIN = 1.16;

/** 表情过渡阻尼，越大切得越快（8 大约 0.4 秒到位） */
const EXPRESSION_DAMPING = 8;

/** 眨眼的闭合时长（秒），以及两次之间的随机间隔范围 */
const BLINK_DURATION = 0.18;
const BLINK_GAP_MIN = 2.2;
const BLINK_GAP_MAX = 5.6;

/**
 * 后端 7 种 emotion → VRM 表情名。
 *
 * 名字都是导出文件里查过的真实名字：happy / sad / surprised / angry 是 VRM 预设，
 * shy(はぅ) / troubled(困った) 是自定义表情。
 * 计划文档里那套「shy 用 happy×0.5 + relaxed×0.3 凑」是模型还没有自定义表情时的旧方案，
 * 现在有真表情了，直接一对一。
 */
const EMOTION_EXPRESSIONS: Record<Emotion, Record<string, number>> = {
    neutral: {},
    happy: { happy: 1 },
    sad: { sad: 1 },
    surprised: { surprised: 1 },
    shy: { shy: 1 },
    thinking: { troubled: 0.9 },
    angry: { angry: 1 },
};

/**
 * 每帧都要写一遍的表情。
 * 只列可能出场的名字，模型里没有的会被 getExpression 跳过（导出的模型里 tears 也在，
 * 想给 sad 加强度的话在 EMOTION_EXPRESSIONS 里加 { tears: 0.4 } 就行）。
 */
const DRIVEN_EXPRESSIONS = [
    "happy",
    "sad",
    "surprised",
    "angry",
    "shy",
    "troubled",
    "tears",
    "aa",
    "ih",
    "ou",
    "ee",
    "oh",
];

/** 说话时轮换的元音，假口型用；接上音频包络后这个表就退化成播放源 */
const MOUTH_VOWELS = ["aa", "ih", "ou", "ee"];

const props = defineProps<{
    emotion: Emotion;
    speaking: boolean;
    /** TTS 实时算出来的张嘴包络；没传就退化成正弦假口型（桌宠早期、或者纯看表情时） */
    mouth?: MouthSignal;
}>();

const emit = defineEmits<{
    ready: [];
    failed: [reason: string];
}>();

const containerRef = ref<HTMLDivElement | null>(null);
const state = ref<"loading" | "ready" | "failed">("loading");
const note = ref("3D 模型载入中…");

// three 的对象不放 ref：它们是长生命周期的可变对象，进响应式系统只会平白多一层 proxy
let renderer: WebGLRenderer | null = null;
let scene: Scene | null = null;
let camera: PerspectiveCamera | null = null;
let vrm: VRM | null = null;
let modelBounds: Box3 | null = null;
let resizeObserver: ResizeObserver | null = null;
let frameId = 0;
const clock = new Clock();

let blinkStartedAt = -1;
let nextBlinkAt = BLINK_GAP_MIN;

function describeError(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}

function fail(reason: string): void {
    state.value = "failed";
    note.value = "3D 模型没起来，先用立绘顶着";
    console.warn("[character] 3D 舞台不可用：", reason);
    emit("failed", reason);
}

function fitCamera(): void {
    if (!camera || !modelBounds) {
        return;
    }

    const size = new Vector3();
    const center = new Vector3();
    modelBounds.getSize(size);
    modelBounds.getCenter(center);

    const halfFov = MathUtils.degToRad(camera.fov) / 2;
    const distanceForHeight = (size.y * FIT_MARGIN) / 2 / Math.tan(halfFov);
    const distanceForWidth = (size.x * FIT_MARGIN) / 2 / (Math.tan(halfFov) * camera.aspect);
    const distance = Math.max(distanceForHeight, distanceForWidth);

    // 相机放在 +Z：Blender 里模型朝 -Y，转成 glTF 的 Y-up 之后就是朝 +Z，正对着是脸
    camera.position.set(center.x, center.y, center.z + distance);
    camera.lookAt(center.x, center.y, center.z);
}

function applySize(): void {
    const container = containerRef.value;
    if (!container || !renderer || !camera) {
        return;
    }

    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);

    // 这台机器只有软件渲染，像素比拉太高会直接卡住
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    fitCamera();
}

/** 眨眼：three-vrm 不自动眨眼，得自己给 blink 打拍子 */
function blinkWeight(elapsed: number): number {
    if (blinkStartedAt < 0) {
        if (elapsed >= nextBlinkAt) {
            blinkStartedAt = elapsed;
        }
        return 0;
    }

    const progress = (elapsed - blinkStartedAt) / BLINK_DURATION;
    if (progress >= 1) {
        blinkStartedAt = -1;
        nextBlinkAt = elapsed + BLINK_GAP_MIN + Math.random() * (BLINK_GAP_MAX - BLINK_GAP_MIN);
        return 0;
    }

    return progress < 0.5 ? progress * 2 : (1 - progress) * 2;
}

/**
 * 口型：张嘴程度优先取 useTts 写的音频包络（0~1），没传就退化成正弦。
 * 元音每 0.4 秒轮换一次，只为了让嘴形不是单纯地开合。
 */
function mouthTargets(elapsed: number, speaking: boolean): Record<string, number> {
    if (!speaking) {
        return {};
    }

    const open = props.mouth
        ? MathUtils.clamp(props.mouth.open, 0, 1)
        : 0.18 + 0.42 * Math.abs(Math.sin(elapsed * 8.5));
    if (open <= 0.01) {
        return {};
    }

    const targets: Record<string, number> = { aa: open };
    const secondary = MOUTH_VOWELS[Math.floor(elapsed * 2.6) % MOUTH_VOWELS.length];
    if (secondary !== "aa") {
        targets[secondary] = open * 0.5;
    }
    return targets;
}

function updateExpressions(delta: number, elapsed: number): void {
    const manager = vrm?.expressionManager;
    if (!manager) {
        return;
    }

    const targets: Record<string, number> = {
        ...EMOTION_EXPRESSIONS[props.emotion],
        ...mouthTargets(elapsed, props.speaking),
    };

    for (const name of DRIVEN_EXPRESSIONS) {
        if (!manager.getExpression(name)) {
            continue;
        }
        const current = manager.getValue(name) ?? 0;
        const target = MathUtils.clamp(targets[name] ?? 0, 0, 1);
        manager.setValue(name, MathUtils.damp(current, target, EXPRESSION_DAMPING, delta));
    }

    // 眨眼不参与阻尼：闭合全程只有 0.18 秒，阻尼会把峰值削掉，眼睛就永远闭不上
    manager.setValue("blink", blinkWeight(elapsed));
}

function tick(): void {
    frameId = window.requestAnimationFrame(tick);
    if (!renderer || !scene || !camera) {
        return;
    }

    // 切窗口回来时 delta 会很大，钳一下免得表情一顿跳
    const delta = Math.min(clock.getDelta(), 0.1);

    if (vrm) {
        updateExpressions(delta, clock.elapsedTime);
        // 头发那 37 条 spring bone 全靠这一句，漏了就是一头僵发
        vrm.update(delta);
    }

    renderer.render(scene, camera);
}

function onModelLoaded(gltf: GLTF): void {
    const loaded = gltf.userData.vrm as VRM | undefined;
    if (!loaded) {
        fail("文件里没有 VRM 扩展数据，是不是导成了普通 glTF？");
        return;
    }

    vrm = loaded;
    // 这个文件是 VRM 1.0，方向本来就是对的：rotateVRM0() 是给 0.x 模型用的，别调
    VRMUtils.removeUnnecessaryVertices(gltf.scene);

    // 蒙皮网格的包围盒在绑定时算不准，开视锥剔除会让角色转到某个角度整个消失
    vrm.scene.traverse((object) => {
        object.frustumCulled = false;
    });

    scene?.add(vrm.scene);
    modelBounds = new Box3().setFromObject(vrm.scene);
    applySize();

    state.value = "ready";
    note.value = "";
    emit("ready");
}

onMounted(() => {
    const container = containerRef.value;
    if (!container) {
        return;
    }

    try {
        renderer = new WebGLRenderer({ alpha: true, antialias: true });
    } catch (error) {
        fail(`WebGL 初始化失败（${describeError(error)}）`);
        return;
    }
    renderer.outputColorSpace = SRGBColorSpace;
    container.appendChild(renderer.domElement);

    scene = new Scene();
    camera = new PerspectiveCamera(30, 1, 0.05, 40);

    // 亮度按这个模型调的：MMD 材质转出来是 metallic=0 / roughness=0.5，
    // 没有环境贴图时全靠这几盏灯，太暗会显得像没上色
    scene.add(new AmbientLight(0xe7ddff, 1.1));
    scene.add(new HemisphereLight(0xe9e0ff, 0x4a3d68, 0.9));

    const keyLight = new DirectionalLight(0xffffff, 1.9);
    keyLight.position.set(1.4, 2.2, 2.6);
    scene.add(keyLight);

    const rimLight = new DirectionalLight(0xc9b6ff, 0.8);
    rimLight.position.set(-1.6, 1.5, -2.2);
    scene.add(rimLight);

    resizeObserver = new ResizeObserver(() => applySize());
    resizeObserver.observe(container);
    applySize();

    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    loader.load(MODEL_URL, onModelLoaded, undefined, (error) =>
        fail(`读不到 ${MODEL_URL}：${describeError(error)}`),
    );

    clock.start();
    frameId = window.requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
    window.cancelAnimationFrame(frameId);
    resizeObserver?.disconnect();
    resizeObserver = null;

    if (vrm && scene) {
        scene.remove(vrm.scene);
        // 贴图、几何体、材质都要显式释放，不然来回切页面会一直涨显存
        VRMUtils.deepDispose(vrm.scene);
    }
    vrm = null;
    modelBounds = null;

    renderer?.dispose();
    renderer?.domElement.remove();
    renderer = null;
    scene = null;
    camera = null;
});
</script>

<style scoped>
.character-stage {
    width: 100%;
    height: 100%;
    overflow: hidden;
}

/* setSize(w, h, false) 不会写 canvas 的内联尺寸，尺寸由这里撑 */
.character-stage :deep(canvas) {
    display: block;
    width: 100%;
    height: 100%;
}

.stage-note {
    position: absolute;
    right: 0;
    bottom: 4px;
    left: 0;
    margin: 0;
    font-size: 11px;
    text-align: center;
    color: rgba(214, 206, 240, 0.55);
    pointer-events: none;
}

.stage-note[data-tone="failed"] {
    color: rgba(240, 200, 200, 0.7);
}
</style>
