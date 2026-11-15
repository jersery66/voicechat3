# Open Questions

状态：**FUTURE DESIGN**

这里只记录当前代码和已锁定原则无法决定的问题；已明确的 authority、opt-in、
state-owner 和 evidence 边界不重复列为 open question。

1. 机构希望在首次使用支持活动前采用何种统一说明/同意文本？这属于产品与机构
   流程，不由当前代码推断。
2. 目标工作站的真实音频设备、VoxCPM2 共存和活动 UI 资源是否满足同时运行，需
   真实硬件测量后决定。
3. `ActivitySessionRecord` 的最终持久化位置和 session/subject reference 形式，
   需结合现有 DataManager schema 与隐私审查确定。
4. 活动中的语音输入是否复用现有 STT turn lifecycle，还是由 ActivityRuntime
   提供独立 capture command，需在 Phase B 的 contract 设计时确认。
5. 机构是否允许某些活动打断 active scale；默认矩阵为不打断，除非明确的用户
   请求和 Policy 规则同时满足。
6. `RECOMMEND_SUPPORT_ACTIVITY` 是否作为未来 `TurnAction` 正式 enum，还是先以
   candidate + existing command adapter 实施，需在 Phase C 结合兼容性决定。

以上问题不能被 Agent、LLM 或 UI 自行默认解决；未决时使用安全的 no-activity
路径。
