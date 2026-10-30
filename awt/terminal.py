"""Interactive terminal edition. All worklog writes use the same local API as UI."""
from datetime import date,datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
import re
from rich.console import Console
from rich.prompt import Prompt,Confirm
from rich.table import Table
from . import db
from .models import CONVERSATION_SOURCES


def minutes(value):
    try:
        amount=Decimal(value)*60
        if not amount.is_finite() or amount!=amount.to_integral_value() or not 0<=amount<=1440:
            raise ValueError()
        return int(amount)
    except (InvalidOperation,ValueError):
        raise ValueError('工时需为0至24小时，且能换算为整数分钟') from None


class Terminal:
    def __init__(self,api,day=None,console=None,ask=None,confirm=None):
        self.api=api;self.console=console or Console(markup=False,highlight=False)
        self.ask=ask or (lambda label,default='',password=False:Prompt.ask(label,default=default,password=password,console=self.console))
        self.confirm=confirm or (lambda label,default=False:Confirm.ask(label,default=default,console=self.console))
        settings=self.get('/settings')
        self.day=day or datetime.now(ZoneInfo(settings['timezone'])).date().isoformat()
        date.fromisoformat(self.day)
        self.estimate_draft=None

    def get(self,path):return self.api.request(path)
    def post(self,path,body):return self.api.request(path,'POST',body)
    def put(self,path,body):return self.api.request(path,'PUT',body)
    def table(self,title,headers,rows):
        table=Table(title=title)
        for h in headers:table.add_column(h,overflow='fold')
        for row in rows:table.add_row(*(str(v) for v in row))
        self.console.print(table)

    def choose(self,title,rows,name=lambda r:r['name'],default=''):
        if not rows:raise ValueError('没有可选择的记录，请检查配置或访问权限')
        self.table(title,['序号','名称'],[(i+1,name(r)) for i,r in enumerate(rows)])
        while True:
            value=self.ask('选择序号（0返回）',default)
            if value=='0':return None
            if value.isdigit() and 1<=int(value)<=len(rows):return rows[int(value)-1]
            self.console.print('请输入列表中的序号。')

    def report(self):
        r=self.get('/reports/'+self.day);c=r['conversations']
        self.console.print(f"{self.day} · {c['session_count']}个会话 · {c['user_messages']}条用户消息 / {c['assistant_messages']}条AI回复 · 工作项{len(r['tasks'])}个")
        self.table('工作项（AI工时为待审核建议）',['序号','标题','项目','小时','来源'],[(i+1,t['title'],t['project'],f"{t['confirmed_minutes']/60:g}",'固定' if t['time_locked'] else 'AI建议' if t['time_source']=='ai_estimate' else '待分配') for i,t in enumerate(r['tasks'])])
        self.console.print(f"合计 {r['total_minutes']/60:g}h / 目标 {r['target_minutes']/60:g}h · {'已确认' if r['confirmed'] else '草稿'}")
        for warning in r['warnings']:self.console.print(warning)
        return r

    def collect(self):
        result=self.post('/collect?date='+self.day,{})
        self.console.print(result);self.report()

    def analyze(self):
        self.console.print('AI正在筛选相关对话并归纳业务工作项，请等待…')
        result=self.post('/analyze?date='+self.day,{})
        self.console.print(result);self.report()

    def add(self):
        while True:
            title=self.ask('人工工作项标题').strip()
            if not title:raise ValueError('标题不能为空')
            project=self.ask('所属项目','未归属项目')
            category=self.ask('分类','沟通')
            duration=minutes(self.ask('工时（小时，0表示待分配）','0.5'))
            details=self.ask('工作内容（可留空）')
            self.post('/tasks',{'date':self.day,'title':title,'project':project,'category':category,'details':details,
                               'confirmed_minutes':duration,'time_locked':duration>0})
            self.console.print('人工事项已保存。')
            if not self.confirm('继续添加人工事项？'):break

    def estimate(self):
        if not (self.estimate_draft and self.estimate_draft[0]==self.day and self.confirm('重试上次估算，并保留人工输入？',True)):
            items=[]
            if self.confirm('分配前添加运维/沟通事项？'):
                while True:
                    category=self.ask('类别（运维/沟通）','沟通')
                    if category not in ('运维','沟通'):raise ValueError('快捷人工事项类别为运维或沟通')
                    name=self.ask('事项简称').strip()
                    duration=minutes(self.ask('固定时长（小时）','0.5'))
                    if not name or duration<=0:raise ValueError('事项名称与正数时长不能为空')
                    items.append({'category':category,'name':name,'minutes':duration})
                    if not self.confirm('继续添加一条？'):break
            self.estimate_draft=(self.day,{'request_id':uuid4().hex,'manual_items':items})
        self.console.print('AI正在命名并按0.5小时分配剩余工时，人工固定时长保留…')
        result=self.post('/reports/'+self.day+'/estimate-time',self.estimate_draft[1])
        self.estimate_draft=None
        for warning in result['warnings']:self.console.print(warning)
        self.report()

    def edit(self):
        r=self.report()
        selected=self.choose('选择工作项',r['tasks'],lambda t:t['title'])
        if not selected:return
        action=self.ask('操作：编辑 / 删除','编辑')
        if action=='删除':
            if self.confirm('确认删除此工作项？'):
                self.api.request('/tasks/'+selected['id'],'DELETE')
            return
        body={'title':self.ask('标题',selected['title']),'project':self.ask('所属项目',selected['project']),
              'confirmed_minutes':minutes(self.ask('工时（小时）',f"{selected['confirmed_minutes']/60:g}"))}
        body['time_locked']=self.confirm('固定此项工时，AI分配时保留？',selected['time_locked'])
        self.api.request('/tasks/'+selected['id'],'PATCH',body)
        self.console.print('已保存。')

    def export(self):
        r=self.report()
        if not r['confirmed'] and self.confirm('按实际工作核对完毕，确认当日日报？'):
            self.post('/reports/'+self.day+'/confirm',{})
        destination=Path(self.ask('Markdown导出路径',str(db.data_dir()/'exports'/f'worklog-{self.day}.md'))).expanduser()
        if destination.exists() and not self.confirm('文件已存在，确认覆盖？'):return
        # Markdown is the only non-JSON API response.
        text=self.api.markdown('/reports/'+self.day+'/export.md')
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(text,encoding='utf-8')
        self.console.print('已导出：'+str(destination.resolve()))

    def settings(self):
        settings=self.get('/settings')
        mode=self.ask('设置：模型 / 来源 / Git用户','模型')
        if mode=='Git用户':
            self.git_users(settings);return
        if mode=='来源':
            for key in CONVERSATION_SOURCES:
                settings[key]['enabled']=self.confirm('启用 '+key+' 采集？',settings[key]['enabled'])
                paths=self.ask(key+' 路径（多条用 | 分隔）','|'.join(settings[key]['paths']))
                settings[key]['paths']=[p.strip() for p in paths.split('|') if p.strip()]
            roots=self.ask('Git仓库根目录（多条用 | 分隔）','|'.join(settings['git_roots']))
            settings['git_roots']=[p.strip() for p in roots.split('|') if p.strip()]
            sources=self.ask('参与AI分析的来源（'+' | '.join(CONVERSATION_SOURCES)+'，可另加git）','|'.join(settings['analysis_sources']))
            settings['analysis_sources']=[p.strip() for p in sources.split('|') if p.strip()]
            self.put('/settings',settings);return
        presets=self.get('/model/presets')
        options=[{'name':v['label'],'key':k,**v} for k,v in presets.items()]
        chosen=self.choose('选择AI供应商',options)
        if not chosen:return
        settings['llm_provider']=chosen['key']
        key=''
        if chosen['key']=='ccswitch':
            settings['ccswitch_config_path']=self.ask('Claude Code settings.json路径（留空自动检测）',settings['ccswitch_config_path'])
            settings['llm_model']=self.ask('覆盖模型名（留空跟随CCSwitch）','')
            settings['llm_protocol']='anthropic'
        else:
            settings['llm_endpoint']=self.ask('完整API地址',chosen['endpoint'] or settings['llm_endpoint'])
            settings['llm_model']=self.ask('模型名称',chosen['model'] or settings['llm_model'])
            settings['llm_protocol']=chosen['protocol'] or settings['llm_protocol']
            key=self.ask('API Key（输入隐藏，留空保留）',password=True)
        settings['llm_enabled']=self.confirm('允许将所选日期脱敏后的工作对话发送给此模型？',settings['llm_enabled'])
        self.put('/settings',settings)
        if key:self.post('/model/key',{'api_key':key})
        if self.confirm('发送简短消息测试模型连接？'):self.post('/model/test',{})
        self.console.print('模型设置已保存；密钥不会在终端回显。')

    def git_users(self,settings):
        repos=self.post('/git/identities',{'roots':settings['git_roots']})
        self.table('Git提交作者（邮箱优先精确匹配）',['仓库','检测身份','当前采集身份'],
            [(r['project'],r['detected']['name']+' <'+r['detected']['email']+'>',r['selected']['name']+' <'+r['selected']['email']+'>') for r in repos])
        action=self.ask('Git用户：自动 / 统一自定义 / 按仓库','自动')
        if action=='自动':settings['git_author']=None
        elif action=='统一自定义':
            current=settings.get('git_author') or {}
            settings['git_author']={'name':self.ask('作者姓名（可留空）',current.get('name','')),
                                    'email':self.ask('作者邮箱（优先匹配，可留空）',current.get('email',''))}
        elif action=='按仓库':
            repo=self.choose('选择Git仓库',repos,lambda r:r['project']+' · '+r['root'])
            if not repo:return
            options=[{'name':'继承默认用户','action':'inherit'},
                     *[{'name':a['name']+' <'+a['email']+'>','author':{'name':a['name'],'email':a['email']}} for a in repo['authors']],
                     {'name':'自定义姓名或邮箱','action':'custom'}]
            chosen=self.choose('选择提交作者',options)
            if not chosen:return
            overrides=settings['git_author_overrides']
            overrides.pop(repo.get('override_key'),None)
            if chosen.get('action')=='inherit':overrides.pop(repo['root'],None)
            elif chosen.get('action')=='custom':
                overrides[repo['root']]={'name':self.ask('作者姓名（可留空）'),'email':self.ask('作者邮箱（优先匹配，可留空）')}
            else:overrides[repo['root']]=chosen['author']
        else:raise ValueError('请选择自动、统一自定义或按仓库')
        self.put('/settings',settings)
        self.console.print('Git用户已保存。仓库单独设置优先；旧提交也按新身份过滤，已审核工作项与人工工时保留。')

    def history(self):
        path=self.ask('历史文件路径（JSON/CSV/XLSX/XLS）')
        since=self.ask('风格参考起始日期','2026-09-15');date.fromisoformat(since)
        result=self.post('/style/import',{'path':path,'since':since,'include_git':self.confirm('参考已关联Git仓库的标题用词？',True)})
        self.console.print(f"历史库 {result['stored_records']} 条，本次新增 {result['added']} 条。{result['style']['message']}")

    def enterprise_settings(self):
        status=self.get('/gitee/enterprise/status');config=status['config']
        key=self.ask('企业版Token（输入隐藏，留空保留）',password=True)
        if key:self.post('/gitee/enterprise/token',{'api_key':key})
        company=self.choose('选择Gitee企业',self.get('/gitee/enterprise/enterprises'))
        if not company:return
        if company['id']!=config['enterprise_id']:config['rules']=[]
        config['enterprise_id']=company['id']
        programs=self.get('/gitee/enterprise/programs?enterprise_id='+str(company['id']))
        while True:
            names={p['id']:p['name'] for p in programs}
            self.table('已设置的标题与项目映射',['序号','方式','文字','项目'],[(i+1,r['mode'],r['pattern'],names.get(r['program_id'],str(r['program_id']))) for i,r in enumerate(config['rules'])])
            action=self.ask('映射操作：添加 / 删除 / 保存','保存')
            if action=='保存':
                self.put('/gitee/enterprise/config',config);self.console.print('企业与映射已保存。');return
            if action=='删除':
                value=self.ask('删除的映射序号')
                if not value.isdigit() or not 1<=int(value)<=len(config['rules']):raise ValueError('映射序号无效')
                config['rules'].pop(int(value)-1);continue
            if action!='添加':continue
            pattern=self.ask('项目前缀、标题关键词或完整标题').strip()
            mode=self.ask('匹配方式：前缀 / 包含 / 完整','前缀')
            if mode not in ('前缀','包含','完整'):raise ValueError('匹配方式无效')
            project=self.choose('选择目标企业项目',programs)
            if project:config['rules'].append({'pattern':pattern,'mode':{'前缀':'prefix','包含':'contains','完整':'exact'}[mode],'program_id':project['id']})

    def publish(self):
        r=self.report()
        raw=self.ask('要推送的工作项序号（逗号分隔；留空选择全部）').strip()
        ids=[]
        if raw:
            indexes=[int(v.strip()) for v in raw.split(',')]
            if any(i<1 or i>len(r['tasks']) for i in indexes) or len(indexes)!=len(set(indexes)):raise ValueError('工作项序号无效或重复')
            ids=[r['tasks'][i-1]['id'] for i in indexes]
        preview=self.post('/gitee/enterprise/preview',{'date':self.day,'task_ids':ids})
        targets=[];programs=preview['programs'];names={p['id']:p['name'] for p in programs}
        for item in preview['items']:
            self.console.print('\n'+item['title'])
            state=item.get('existing')
            if state and state['status'] in ('created','pending','uncertain'):
                self.console.print('已创建，跳过：'+state['url'] if state['status']=='created' else '创建结果待核实，不会自动重发。')
                if state['status']!='created' and self.confirm('输入已存在的Gitee工作项数字ID核对？'):
                    self.post('/gitee/enterprise/resolve',{'enterprise_id':preview['enterprise_id'],'task_id':item['task_id'],'issue_id':int(self.ask('Gitee工作项数字ID'))})
                continue
            program_id=item['program_id']
            self.console.print('项目匹配：'+names.get(program_id,'未匹配')+' · '+item['match_reason'])
            if not program_id or self.confirm('更换目标项目？'):
                chosen=self.choose('选择目标项目',programs)
                if not chosen:continue
                program_id=chosen['id']
            issue_type=None;type_name='项目默认任务类型'
            if self.confirm('选择工作项类型（否则使用项目默认任务类型）？'):
                values=self.get(f"/gitee/enterprise/issue-types?enterprise_id={preview['enterprise_id']}&program_id={program_id}")
                chosen=self.choose('工作项类型',values)
                if not chosen:continue
                issue_type=chosen['id'];type_name=chosen['name']
            self.console.print('工作项类型：'+type_name)
            self.console.print(re.sub(r'\n*<!-- awt-work-item:[^>]+ -->$','',item['description']))
            targets.append({'task_id':item['task_id'],'program_id':program_id,'issue_type_id':issue_type})
        if not targets:self.console.print('没有需要创建的工作项。');return
        self.table('将创建的Gitee企业工作项',['标题','项目','预估小时'],[(i['title'],names[t['program_id']],f"{i['minutes']/60:g}") for t in targets for i in preview['items'] if i['task_id']==t['task_id']])
        self.console.print('只发送上述预览内容，工时写入预估工时；不会向任何Gitee代码仓库提交。')
        if not self.confirm(f"确认在企业 #{preview['enterprise_id']} 创建上述 {len(targets)} 条工作项？",False):
            self.console.print('已取消，没有发送创建请求。');return
        result=self.post('/gitee/enterprise/publish',{'batch_id':preview['batch_id'],'confirmed':True,'targets':targets})
        self.table('创建结果',['本地工作项','状态','链接/反馈'],[(i['task_id'],i['status'],i['url'] or i['error']) for i in result['items']])

    def change_date(self):
        value=self.ask('工作日期（YYYY-MM-DD）',self.day);date.fromisoformat(value);self.day=value

    def run(self):
        actions={'1':self.report,'2':self.collect,'3':self.analyze,'4':self.add,'5':self.estimate,'6':self.edit,'7':self.export,
                 '8':self.settings,'9':self.history,'10':self.enterprise_settings,'11':self.publish,'12':self.change_date}
        while True:
            self.console.print(f'\nWork Tracker · 终端版 · {self.day}')
            self.console.print('1 工作项与对话统计  2 采集日志  3 AI归纳工作项  4 添加人工事项\n5 AI分配工时  6 编辑/删除  7 确认与导出  8 模型/来源/Git用户设置\n9 导入历史风格  10 企业与标题映射  11 预览推送企业工作项  12 切换日期  0 退出')
            try:
                choice=self.ask('选择功能','1')
                if choice=='0':return
                if choice not in actions:self.console.print('请输入菜单序号。');continue
                actions[choice]()
            except (ValueError,OSError) as exc:self.console.print('操作未完成：'+str(exc))
            except KeyboardInterrupt:self.console.print('\n本次操作已中断。若创建请求已发出，请核对结果后再重试。')
