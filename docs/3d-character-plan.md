# 3D 形象集成方案（Q 版遐蝶 → 主应用 → 桌宠）

> 2026-10-15。已定的四件事：**用 VRM 渲染**（three + `@pixiv/three-vrm`，不直接渲 PMX）、
> **用 Q 版**（`public/model/xiadie01`）、**先在主应用做通再搬进桌宠**、
> **模型不进分发包**（授权写着"请勿二次配布"）。
>
> **当前进度：主应用这一轮已经做完并验证过**（§0~§3 是"已完成"的记录，§4 之后还是待办）。

## 0. 素材与转换（已完成）

两张 PMX 的体检结论（证据见 `frontend/_stage/pmx-report.txt`）：

| | xiadie01 Q 版 | xiadie02 正比 |
| --- | --- | --- |
| 贴图 | 6/9 被引用且都在 ✓ | **17 张一张都没被引用** ✗ |
| 表情 morph | 眨眼 / 笑 / 怒 / 温柔 / 哭 ✓ | 只搜到 1 个 |
| 结论 | **可用** ✓ | 开箱不可用 ✗ |

原生 VRM 不在本机（Downloads / Desktop / Documents / D:\ / 项目里都搜过），
所以走了 Blender 转换。**Blender 5.0 上两个插件都能用**（原来估的最大风险不成立 ✓）：

- `bl_ext.blender_org.mmd_tools`（导入 PMX）
- `bl_ext.blender_org.vrm`（导出 VRM）

### 产出

| 文件 | 说明 |
| --- | --- |
| `frontend/public/model/character/xiadie.vrm` | 31.7 MB，VRM 1.0，**应用加载的就是它** |
| `frontend/_stage/xiadie-vrm4/xiadie.blend` | 4.2 MB，双击能用 Blender 打开看 |
| `frontend/_stage/xiadie-vrm4/report.json` | 转换过程报告（材质逐条判定、表情绑定） |
| `frontend/_stage/vrm4-final.txt` | 导出文件的验收报告（骨骼/表情/材质/meta 全文） |
| `frontend/_stage/blender_pmx_to_vrm.py` | 可复现脚本，换模型重跑它就够了 |

```powershell
& 'D:\blender\blender.exe' --background --python frontend\_stage\blender_pmx_to_vrm.py -- `
  frontend\public\model\xiadie01\...\ChibiCastorice_MMD_V1.0_release.pmx `
  <out.vrm> <report.json> <out.blend>
```

### 转换里两个坑（都修了，别再踩）

1. **材质全黑**：直接导出的 VRM 里 `baseColor` 是纯黑、贴图被塞进 `emissive`
   —— glTF 导出器读不懂 MMD 的 `MMDShaderDev` 节点组。
   必须先跑 `mmd_tools.convert_materials(use_principled=True)`。
2. **整个角色半透明鬼影**：转完材质后 16 个材质会全变成 `alphaMode=BLEND`，
   three.js 遇到 BLEND 会把 `depthWrite` 关掉。
   脚本按「该材质实际用到的 UV 区域里贴图 alpha 长什么样」逐材质判定：
   全不透明 → OPAQUE、只有硬边抠洞 → MASK、有半透明渐变 → BLEND。
   结果：14 个 OPAQUE，`face_dark_gradient` / `front_hair_face_shadow` / `emoji` 三个 BLEND。

导出文件的实际内容（都是查文件查出来的）：53 根 humanoid 骨骼、
37 个 morph target、18 个 preset 表情 + 3 个自定义表情、
37 条 spring bone 链 + 27 个碰撞体（从 MMD 物理转来，头发会飘）、
6 张贴图内嵌、16 个材质（metallic=0 / roughness=0.5）、meta 里写了作者与授权
（`allowRedistribution: false`、`© miHoYo`）。

## 1. 渲染组件（已完成）

`src/components/character/CharacterStage.vue`：

```
props:  emotion: Emotion          ← 复用现有 currentEmotion
        speaking: boolean         ← 复用现有 tts.isPlaying
        mouth?: MouthSignal       ← 0~1 张嘴程度，来自 useTts（见 §3），不传就退化成正弦假口型
内部：  canvas + renderer + scene + camera + VRM
```

要点（下面几条和最初的设想有出入，以实际为准）：

- **不调 `VRMUtils.rotateVRM0()`**：那是给 VRM 0.x 的，我们导出的是 1.0，方向本来就是对的
  （实测眼睛 z = +0.052、头 z = -0.018，脸朝 +Z，相机放 +Z 正对脸）。
- **眨眼得自己写**：three-vrm 不自动眨眼。组件里用随机间隔 + 0.18 秒闭合循环驱动 `blink`，
  且这一路**不能走阻尼**（阻尼会把峰值削掉，眼睛永远闭不上）。
- 相机按模型包围盒自动取景（按容器宽高比同时算竖直/水平两个方向的距离取大的），
  容器尺寸变化由 `ResizeObserver` 接管，卸载时 `VRMUtils.deepDispose()` 释放显存。
- 光照按这个模型调：环境光 + 半球光 + 主光 + 边缘光
  （材质是 metallic=0 / roughness=0.5，没有环境贴图时全靠这几盏灯）。
- 加载失败 → 角落一行提示 + 回退立绘；`CharacterPanel.vue` 里那张 `<img>` 立绘保留着，
  模型就位后淡出 0.5 秒。

## 2. 表情映射（emotion → VRM expression）

名字都是导出文件里真实存在的（§0 的验收报告里能对）：

| 后端 emotion | VRM expression | 说明 |
| --- | --- | --- |
| neutral | （全部归零） | |
| happy | `happy` | 预设，绑「笑い」 |
| sad | `sad` | 预设，绑「悲しい」；想加强度可加 `tears`(涙) |
| surprised | `surprised` | 预设，绑「驚き」 |
| angry | `angry` | 预设，绑「怒り」 |
| shy | `shy` | **自定义**表情，绑「はぅ」 |
| thinking | `troubled` | **自定义**表情，绑「困った」 |

约定：

- 切换走阻尼过渡（`MathUtils.damp`，λ=8，约 0.4 秒到位）——不做过渡会"跳脸" ✗
- 每帧写一遍目标值，模型里没有的表情用 `getExpression()` 跳过，不会报错
- 数据来源是 `useChat.currentEmotion` ✓ **零后端改动**（它 5 秒后自己回 neutral，组件不用管重置）

## 3. 口型（已完成）

`useTts` 从 `<audio>` 换成了 Web Audio 播放，顺便把包络算出来：

- 每段 `tts_audio_chunk` → `decodeAudioData` → 按 **60Hz** 求 RMS → 
  用 **95 分位**归一化（用最大值归一化的话，一个爆音就把其余嘴部动作压扁）→ 开 0.6 次方
- 播放时用 `AudioContext.currentTime` 定位包络下标，每帧写 `mouth.open`；
  张得快（0.55）收得慢（0.22），看起来才像在说话
- **`mouth` 故意是可变对象不是 `ref`**：60fps 的数据走响应式只会让上层白重渲染，
  读它的地方是渲染组件自己的 rAF 循环
- `stop()`（打断）时口型立刻归零；播完 `isPlaying` 落回 false
- 用户点开语音开关的那次点击里预热 `AudioContext`：实测首播延迟从 **0.96s → 0.32s**
  （凭空多出来的那 1 秒是 AudioContext 冷启动，`decodeAudioData` 本身只要 6~14ms）
- 解不开的格式（比如 pcm）自动退回原来的 `<audio>` 路径，只是口型不动

实测曲线（`_stage/probe3d/tts-report-warm.json`）：响段峰值 1.0、静音段 0.0、淡出段递减、
结尾归零 ✓

## 4. 桌宠那一步（待办）

- 把 `CharacterStage.vue` 搬进 `pet/`（它从裸 WebGL 换成 three —— 就是 pet/README 的第二步）
- 那时才需要定：**WS 归谁**（桌宠自己聊 vs 主应用转发）、区域穿透、以及
  `pet/RESULTS.md` 清单里"透明窗口 + WebGL"那几个坑
- ⚠️ **主应用这一轮验不到透明合成**：主应用是普通窗口（不透明、有边框），
  透明/穿透/置顶那些问题要等搬进桌宠才会冒出来——别以为验过了

## 5. 依赖与体积

- 新增 `three` + `@pixiv/three-vrm` + `@types/three`（dev）→ 渲染层 bundle 实测 **2.44 MB** ✓
- 模型 31.7 MB **不进分发包** → `public/model/character/xiadie.vrm`
  （出分发版时排除；再想压体积可以把 4096² PNG 贴图重新编码，能到 8 MB 上下）
- 已清理：`pixi.js`、`untitled-pixi-live2d-engine`（确认没有任何引用后卸掉）✓
- 还没动：`public/xiadie/`（2.4 MB 的 Live2D 模型）、`public/model/xiadie02`（正比那套贴图没接上）

## 6. 验收

已验证（可复现，工具在 `frontend/_stage/probe3d/`）：

- `lint` / `typecheck` / `build` 全过
- 真机跑一遍（隐藏 Electron 窗口 + Vite dev server）：
  WebGL 2.0 起得来、模型 255 ms 加载完、53 根骨骼 / 21 个表情 / 87 个 spring 关节都在、
  脸朝相机、剪影占画布 34.7%、表情赋值后 3.25% 像素变化（说明真在驱动网格）
- 口型曲线跟着波形走（见 §3）

需要人眼确认：观感（`_stage/xiadie-vrm4/preview/*.png`、`_stage/probe3d/probe-shot.png`）。

还没做：分发包排除模型文件。

## 7. 时间与风险

| 项 | 状态 |
| --- | --- |
| Blender 5.0 插件兼容性 | ✓ 已确认可用（原来的最大不确定项） |
| 模型转换（材质 / 表情 / alpha） | ✓ 已完成并验收 |
| 渲染组件 + 接线 | ✓ 已完成 |
| 表情 + 口型 | ✓ 已完成（口型参数若要按耳朵调，改 §3 里那两个系数） |
| 搬进桌宠 + 桌面壳 | 待办，透明窗口 + WebGL 要在那轮重验 |
| 打包时排除模型 | 待办 |
