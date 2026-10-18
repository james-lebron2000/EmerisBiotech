"""Landing metrics are actual workspace records, separate from bundled demos."""
from vbt.workbench import Workbench,page
from vbt.market import Market
from html.parser import HTMLParser


def test_dashboard_asset_entry_uses_company_research_workbench(tmp_path):
 class Links(HTMLParser):
  def __init__(self, value):
   super().__init__(); self.href=None; self.links=[]; self.feed(value)
  def handle_starttag(self, tag, attrs):
   if tag=='a': self.href=dict(attrs).get('href')
  def handle_data(self, data):
   if self.href: self.links.append((self.href,data))
  def handle_endtag(self, tag):
   if tag=='a': self.href=None
 app=Workbench(tmp_path/'w',tmp_path/'s')
 links=Links(page(app,'/',{})).links
 assert ('/assets','检索临床资产') in links
 assert ('/assets','选择公司与登记用药') in links
 assert ('/market','唯一登记试验') in links


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


def test_backup_scope_is_local_to_historical_research(tmp_path):
 import json
 app=Workbench(tmp_path/'w',tmp_path/'s')
 app.workspace.mkdir(parents=True,exist_ok=True)
 (app.workspace/'cache-info.json').write_text(json.dumps({'scope':'备份截至 2026-09-25 <script>bad</script>'}))
 body=page(app,'/',{})
 assert body.index('市场与登记 · 当前本机采集') < body.index('历史分析与研究快照 · 备份记录')
 assert body.index('工作区研究记录') < body.index('备份截至')
 assert '按保存编号展示部分记录' in body and '最近研究记录' not in body
 assert '<script>bad</script>' not in body and '&lt;script&gt;' in body
 assert '提出研究问题' not in body and '记录研究问题' in body
 history=page(app,'/records',{})
 assert '历史研究记录的备份范围' in history and '备份截至' in history
