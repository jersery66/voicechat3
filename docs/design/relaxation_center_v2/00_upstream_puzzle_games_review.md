# Leisure Games V2 — Upstream puzzle-games design review

状态：**DESIGN REVIEW ONLY / NO V1 RUNTIME CHANGE**

审查基线：

- V1 immutable tag: `relaxation-center-v1-pre-hardware-20260818`
- V1 closure commit: `72942b2f6b7d1ceaf2d52141b270f34bf8e13e0d`
- 本审查不移动 tag、不修改 Catalog、不增加运行时依赖、不实现 V2 游戏。

## 结论

当前四个 V1 leisure 项目保留不动。它们是轻互动玩具，已经完成软件闭环；
V2 应另行增加一个“轻量益智游戏库”，而不是继续给泡泡/落叶增加分数、关卡或
竞争机制。

建议顺序：

1. **Untangle / 解开线团**
2. **Net / 连通网络**
3. **Light Up / 点亮空间**
4. **Bridges / 岛屿连桥**

Pattern / Nonogram 作为第二批；Sokoban 暂缓，除非以后明确加入无限 Undo、Restart
和无惩罚回退。

## 上游来源与许可证审计

### Simon Tatham’s Portable Puzzle Collection

- 官方来源：<https://www.chiark.greenend.org.uk/~sgtatham/puzzles/>
- 官方源码审查 commit：`3c3632259d298ab62aafa8a5858823569ab1af46`
- 主要文件：`untangle.c`、`net.c`、`lightup.c`、`bridges.c`、`pattern.c`
- 许可证：MIT；官方页面明确说明整个 collection 以 MIT license 分发。
- 结构：C backend + 可移植 front-end interface；不是 Python/PySide6 组件。
- 可复用范围：算法/数据结构/交互机制的合法移植，保留版权和 MIT notice。
- 不可直接带入：C executable、C build chain、原生 front-end、图标/素材、浏览器
  或其他上游运行时。

官方源码中已确认的实现事实：

- Untangle 使用点与边的图结构、线段相交判断、随机生成和拖拽节点；预设点数为
  6/10/15/20/25。
- Net 使用网格 tile 旋转和网络连通性求解；默认预设包含 5×5、7×7、9×9、11×11，
  并提供 unique-solution 生成控制。
- Light Up 使用灯光可见性、黑格数字约束和 generator/solver；官方预设包含
  7×7 easy/tricky/hard 以及更大棋盘。
- Bridges 使用岛屿度数、横纵桥、交叉互斥和全图连通性；官方默认最多两条平行桥，
  并有 7×7/10×10/15×15 与 easy/medium/hard 预设。
- Pattern 是带行列 clues 的 Nonogram 生成/求解器，具有唯一解检查，文字负荷高于
  前四项。

### Nonogram-Maker

- 来源：<https://github.com/kniffen/Nonogram-Maker>
- 审查 commit：`de98a2e86b24a60635a0e7df0b089041bc8d1b43`
- 许可证：MIT，Copyright 2018–2022 Kniffen。
- 结构：JavaScript/browser app，包含生成 clues、solver 和鼠标交互。
- 决定：只作为 Nonogram 生成/solver 思路参考；不复制 JS、不引入 browser runtime，
  不复制 screenshot 或其他素材。

### PuzzleKit

- 来源：<https://github.com/SmilingWayne/puzzlekit>
- 审查 commit：`9ef8ebbb262039401d72e37af0d23f9c9b46a5b0`
- 许可证：MIT，Copyright 2025 SmilingWayne。
- 结构：Python solver library，但明确依赖 OR-Tools CP-SAT。
- 决定：不作为 voicechat3 runtime dependency；只参考 puzzle taxonomy、验证和唯一解
  组织方式。引入 OR-Tools 对四个轻量游戏不划算。

### GPL Puzzle collection

- 来源：<https://github.com/sidhant947/Puzzle>
- 审查 commit：`697c80f8df3dd5369a31dd1f3760df7fccf05784`
- 许可证：GPL-3.0。
- 决定：只观察玩法和信息架构；不复制代码、素材、文案或 runtime，不把其
  “brain training/attention”产品表述带入本项目。

## V2 候选评估

| 游戏 | 交互 | 生成/验证 | 认知负荷 | 建议 |
| --- | --- | --- | --- | --- |
| Untangle | 拖动节点 | 图生成 + crossing detection | 中 | 第一优先 |
| Net | 点击旋转 tile | 网络生成 + 连通/唯一解检查 | 中 | 第二优先 |
| Light Up | 点击放灯 | 可见性 + 数字约束 + solver | 中高 | 第三优先 |
| Bridges | 拖拽岛屿连桥 | 度数/交叉/连通 solver | 高 | 第四优先 |
| Pattern | 点击填格 | clues + 唯一解生成 | 高 | 第二批 |
| Sokoban | 移动/推箱 | 关卡可解性 | 高且有不可逆死局 | 暂缓 |

## Native port contract

V2 获得实施授权后，必须继续沿用 V1 的边界：

- Python + PySide6 native widgets；
- deterministic seeded model，UI 只是渲染和输入适配；
- 无分数、计时压力、排行榜、奖励、失败羞辱或心理/注意力/复吸指标；
- 每局可退出、重置，推荐提供 Undo；
- 生成器必须有 deterministic validation，不能把“能显示”当作“有解”；
- 不嵌入 C、浏览器、WebView、Phaser、pygame 或 OR-Tools；
- 不改变 Agent、TurnPolicy、ScaleRuntime、SessionEngine、RelaxationRuntime；
- 用户仍然从 Center 选择具体内容，Agent 不选择某个 puzzle；
- 若实际复用 MIT 源码，记录 repo、commit、文件、复用范围并保留 notice；
- 不复用上游图标、字体、音频、截图或其他未单独核验的 assets。

## 建议的 V2 实施拆分

### V2-A — Untangle

只实现本地 `UntangleModel` 和 PySide6 widget：节点、边、拖拽、相交标记、完成
判断、seed、6/10 点 preset、reset/undo。先不接 Agent/TurnPolicy，不改变 V1
Catalog。

### V2-B — Net

实现 tile bitmask、旋转、连通性判断、seeded generator 和 5×5/7×7 preset。先
禁用 wrapping、障碍和 hard mode，确保短局和唯一解验证可靠。

### V2-C — Light Up / Bridges

在前两项的 puzzle-model contract 稳定后再做。默认只提供 easy/normal，所有非法
状态使用中性视觉提示，不使用红色羞辱或失败惩罚。

### V2-D — Pattern / Nonogram

单独评估行列 clues 的中文界面负荷和屏幕尺寸；可参考 Nonogram-Maker，但仍做
PySide6 native rewrite。

## 当前授权状态

```text
V1 runtime:
SOFTWARE CLOSED / PRE-HARDWARE

V2 upstream review:
COMPLETE / DESIGN ONLY

V2 implementation:
NOT STARTED

Real hardware:
NOT RUN
```

下一步应先由产品侧确认是否授权 V2-A Untangle；在此之前不修改 V1 tag 对应的
runtime，不把任何 V2 candidate 写入 V1 Catalog。
