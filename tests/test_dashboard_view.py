"""Landing metrics are actual workspace records, separate from bundled demos."""
from vbt.workbench import Workbench,page
from vbt.market import Market


def test_dashboard_uses_actual_registry_and_preserves_empty_history(tmp_path):
 app=Workbench(tmp_path/'w',tmp_path/'s')
 initial=page(app,'/',{})
 assert '临床资产研究控制台' in initial and '尚无成功查询记录' in initial
 assert '回顾性研究演示与待验证草案' in initial and '不能互相替代' in initial
 m=Market(app.workspace,app.state);m.add_company('US','TEST','Synthetic',['Synthetic'],'Synthetic fixture')
 updated=page(app,'/',{})
 assert '已关联证券</span><strong class="metric">1</strong>' in updated
 assert '研究历史记录</span><strong class="metric">0</strong>' in updated
 assert not app.records()
