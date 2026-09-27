"""Project mission and evidence milestones; never infers scientific approval."""
MISSION='用可追溯、可校准的二期／三期临床结果预测，寻找并验证扣除成本后的投资优势。'
MILESTONES=[
 {'id':'data','title':'建立可回放的临床事件样本','acceptance':'明确试验/资产/适应症与标签；每个特征有历史公开时间，覆盖失败与待定项目，核验商业使用权。','next':'冻结首个适应症、预测时点、入排规则和数据交接清单。'},
 {'id':'clinical','title':'证明临床预测优于简单基准','acceptance':'二期和三期分开；滚动时间外评估，隔离同资产；报告概率校准、Brier、log loss、覆盖率和不确定性。','next':'先比较阶段/适应症基准与简单模型，再检验 AI 的增量价值。'},
 {'id':'investment','title':'证明预测可以转化为投资收益','acceptance':'固定策略后做时间外验证；含首次可交易价格、失败/退市样本、成本、流动性和回撤；与基准比较。','next':'确定市场范围与价格来源，形成冻结的模拟组合协议。'},
 {'id':'prospective','title':'完成前瞻模拟验证','acceptance':'结果公开前封存预测与决策；完整记录弃权和失败，按事先确定的规则评估，禁止事后选样。','next':'登记未来事件与预测截止时间，先做模拟观察。'},
 {'id':'cloud','title':'交付稳定可访问的私有工作台','acceptance':'远端登录、持久化、记录迁移、备份恢复及运行监控通过实际验收。','next':'确认云主机与域名；部署包不等于服务已上线。'}]
STATES={'planned':'待推进','in_progress':'推进中','blocked':'有阻塞','evidence_submitted':'已提交证据，待独立审查'}

def validate_update(app,milestone,state,author,note,reference):
    if milestone not in {m['id'] for m in MILESTONES} or state not in STATES:raise ValueError('未知里程碑或状态')
    if not 1<=len(author.strip())<=100 or not 10<=len(note.strip())<=10000:raise ValueError('填写记录人及至少十个字符的进展说明')
    if len(reference)>300:raise ValueError('证据引用过长')
    if reference and reference not in {r['id'] for r in app.records()}:raise ValueError('引用的研究记录不存在')
    if state=='evidence_submitted' and not reference:raise ValueError('提交证据必须关联已有研究记录；不自动视为验收通过')
