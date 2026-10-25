"""Durable one-shot, real-world ABCD task rotation.

Only this store owns claim/retire. A failed API call still consumes its prompt.
The historical prompt remains in Request History and the claim audit for replay
diagnostics, but cannot return to the available pool after page refresh.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

POOL_VERSION = "abcd-one-shot-v198"

# Separate creative topics, not a fixed prompt with a random diagnostic marker.
A_TOPICS = (
    ("山间骑行驿站", "在昆明滇池周边经营一家为骑行者服务的维修、补给与咖啡驿站", "租车周转、维修产能、季节收入"),
    ("微型陶艺工坊", "在苏州开设兼售原创器物的社区陶艺教学工作室", "窑炉排期、耗材损耗、课程预约"),
    ("亲子自然教育营地", "在杭州郊区经营周末亲子自然探索营地", "场次容量、天气风险、师资排班"),
    ("城市植物诊所", "在深圳开设提供植物养护和上门诊疗服务的小型门店", "上门工单、养护周期、订阅复购"),
    ("移动修鞋与皮具护理", "在南京经营上门取送的皮鞋修复与皮具护理服务", "取送线路、复修率、人工工时"),
    ("独立黑胶唱片空间", "在厦门运营黑胶唱片零售与小型试听活动空间", "库存周转、试听场次、会员付费"),
    ("生态水族造景室", "在青岛开设水族箱造景、维护和租赁工作室", "设备维护、客户合约、活体风险"),
    ("社区烘焙共享厨房", "在武汉运营供独立烘焙师预约的共享商用厨房", "时段利用率、清洁验收、食安合规"),
    ("手工装帧书店", "在西安开设小众图书装帧修复和独立出版空间", "工单报价、教材零售、工作坊利润"),
    ("露营装备洗护店", "在重庆经营户外露营装备清洗、维修和季节寄存服务", "淡旺季现金流、库位周转、质检"),
    ("宠物友好摄影棚", "在长沙经营宠物主题摄影、选片及周边印制工作室", "预约取消、素材交付、拍摄转化"),
    ("社区手作香氛吧", "在福州经营天然香氛调配课程及个性化礼品工作室", "原料成本、课程座位、礼盒毛利"),
)
B_TOPICS = (
    ("深海邮局", "小潜水员阿珂、红色氧气背包与贝壳指南针", "珊瑚信箱", "海流中的旧信", "海底邮局的灯"),
    ("月球植物园", "园丁小安、银色手套与黄色种子盒", "陨石温室", "会发光的幼苗", "双月下的归途"),
    ("沙漠行星列车", "旅人诺兰、蓝围巾与铜制怀表", "沙丘中的车站", "流沙之桥", "星云列车驶向黎明"),
    ("萤火虫钟表店", "女孩若冬、绿色斗篷与皮质工具包", "森林钟表铺", "遗失的第十三小时", "时间树开花"),
    ("雪山云鲸", "男孩洛奇、红帽子与银铃", "冰雪山谷", "鲸鱼背上的云", "极光再相会"),
    ("机械蜂鸟花园", "小发明家伊莱、护目镜与蓝铜工具盒", "齿轮温室", "蜂鸟的秘密", "花钟再次鸣响"),
    ("漂流群岛剧院", "演员星悠、紫披肩与圆形胸针", "云梯上的舞台", "被风吹走的剧本", "群岛最后一幕"),
    ("熔岩玻璃小镇", "学徒米娅、橘色围裙与绿水晶手环", "火山玻璃工坊", "会说话的器皿", "透明晨曦"),
    ("森林影子博物馆", "少年瑞亚、羽毛帽与木质照相机", "倒挂的展览馆", "消失的倒影", "树影归位"),
    ("极地灯塔快递", "信使陶陶、星纹手套与条纹邮包", "浮冰快递站", "风暴中的包裹", "北极光收件人"),
    ("迷你巨人厨房", "厨师小薇、格纹帽与红色量勺", "巨树烘焙室", "会走路的面团", "甜点节夜晚"),
    ("雨滴城市天文台", "观星者苏诺、黄雨衣与透明星盘", "玻璃雨塔", "倒映的星座", "晴空升起"),
)
C_TOPICS = (
    ("山地自行车嘉年华", "山地赛道与安全接驳", "赛程、志愿者班表和车辆资源"),
    ("未来农场创新展", "温室示范区与采购商对接", "展商日程、设备清单和赞助权益"),
    ("城市旧书交换节", "跨社区旧书循环和作者沙龙", "摊位日程、图书分类和物流资源"),
    ("低碳婚礼主题展", "环保婚礼品牌和预约体验", "参展日程、展位库存和赞助权益"),
    ("高校机器人公开赛", "机器人竞赛与学生创客展示", "赛项赛程、裁判场次和设备备件"),
    ("水岸夜间光影展", "沿河装置艺术和公众导览", "展期排班、场地风险和电力资源"),
    ("城市宠物领养日", "救助机构、宠物健康咨询和公益活动", "领养流程、摊位排班和现场物资"),
    ("独立电影巡回周", "影展排片、导演交流和城市剧院", "放映日程、版权授权和场地资源"),
    ("乡村非遗手作市集", "手工艺摊位、现场教学和乡村旅游", "展位安排、导师班表和供应资源"),
    ("天文科普观星营", "夜间观测、科普讲座与交通接驳", "观测日程、天气预案和望远镜设备"),
    ("微型戏剧街区周", "多剧场小型演出与观众路线", "演出场次、剧团预算和票务分配"),
    ("社区运动健康周", "家庭体育活动及公益健康咨询", "活动日程、教练班表与运动器材"),
)
D_TOPICS = (
    ("纸灯港的失踪地图", "1931年的海港纸灯镇", "每逢涨潮会消失的航海图", "测绘师岑青", "第二章：无声潮汐"),
    ("月影温室之谜", "1912年的高山庄园", "只在月食时开放的禁区温室", "植物学家艾琳", "第二章：午夜花粉"),
    ("机械花街案", "1924年的钟表工业城", "每个星期日逆行一次的机械花钟", "修表师顾岚", "第二章：齿轮证词"),
    ("雾雪列车密函", "1907年的跨境夜间列车", "某节车厢里不断改写的匿名信", "列车员小满", "第二章：空白车票"),
    ("旧影院的最后一帧", "1940年的港口电影城", "放映结束后消失的观众座位", "剪辑师林雀", "第二章：胶片逆光"),
    ("水下图书馆的钥匙", "1921年的湖畔古镇", "退潮才出现的沉没藏书室", "潜水学者顾然", "第二章：无人借阅"),
    ("天台无线电谜讯", "1937年的高原小城", "无人发送却每天响起的电台求救声", "无线电师阿澄", "第二章：静默频率"),
    ("百年剧院假面", "1919年的戏剧之都", "每一场演出都新增的匿名演员", "舞台监督周黎", "第二章：第七张面具"),
    ("遗失的气象日报", "1934年的暴雨港湾", "总能提前一天刊出的灾难天气预报", "记者叶砂", "第二章：被划去的风向"),
    ("镜湖信使", "1927年的山区邮政小镇", "镜中倒影带来的无邮戳密信", "邮差乔松", "第二章：封蜡破裂"),
    ("琥珀博物馆疑云", "1909年的海岛博物馆", "馆藏昆虫标本里变化的死亡时间", "修复师陆柯", "第二章：透明墓志"),
    ("星象台失眠者", "1932年的山顶观象城", "每晚多出一颗的不存在的星星", "观测员晚宁", "第二章：观象记录"),
)

def build_case(scene: str, serial: int) -> dict:
    choices = {"A": A_TOPICS, "B": B_TOPICS, "C": C_TOPICS, "D": D_TOPICS}
    if scene not in choices:
        raise ValueError("Invalid scene")
    subjects = choices[scene]
    index = serial % len(subjects)
    epoch = serial // len(subjects)
    topic = subjects[index]
    # New constraints each rotation, without artificial IDs in the actual prompt.
    months = (9, 12, 18, 24, 30)
    people = (120, 200, 280, 360, 440)
    budget = (32, 48, 65, 80, 110)
    if scene == "A":
        title, context, challenge = topic
        prompts = [f"我打算{context}，项目名为「{title}」。请交付可用于团队落地的完整经营方案，包含目标客群、市场和竞争、场地、服务与产品结构、供应链、成本定价、现金流、员工排班、获客方式、主要风险及{months[epoch % 5]}个月的运营计划。重点建立{challenge}模型，经营投入上限为{budget[epoch % 5] + 5 * epoch}万元。请使用至少两张真实Markdown表格，提供一段能够独立运行的Python示例并说明运行方法，用数学表达式说明核心商业计算。不要只给提纲，最后以《执行任务表》收尾，列出至少十项行动、负责人、时间和验收标准。"]
        end_anchor = "执行任务表"
    elif scene == "B":
        title, hero, picture1, picture2, picture3 = topic
        prompts = [f"请为独立原创绘本系列第{epoch + 1}册《{title}》编写有开端、危机与结局的三幕故事。主角是{hero}，三幕中形象、随身物品保持一致。请实际生成并分别交付三张独立、可以下载的{('3:4', '4:5', '2:3')[epoch % 3]}竖版精美插画，依次为《{picture1}》《{picture2}》《{picture3}》，各有不同的构图和情绪，不要把三张拼成一张，也不要只给绘图提示词、代码或本地文件路径。结合画面逐幕说明故事。"]
        end_anchor = ""
    elif scene == "C":
        title, goal, resources = topic
        prompts = [f"我要组织「{title}」，目标是{goal}，预计参与规模为{people[epoch % 5] + 25 * epoch}人，请提供完整执行计划并实际交付4份独立可下载文件：（1）包含预算明细和计算公式的XLSX工作簿；（2）不少于8页、有招商价值的PPTX演示稿；（3）包含{resources}的数据有效JSON文件；（4）将上述文件与阅读说明打包的ZIP压缩包。请在正文解释商业逻辑、人员分工、风险及备选方案。不能把文字代码、伪链接或本地路径当成文件交付。"]
        end_anchor = ""
    else:
        title, setting, secret, hero, chapter = topic
        prompts = [
            f"请开发复古悬疑互动游戏系列第{epoch + 1}部《{title}》，故事发生在{setting}，核心谜团是{secret}。设计完整的世界观和调查规则，以{hero}为关键人物，另外设计两名性格冲突的角色、五条互有关联的线索、第一章的完整调查剧情、三种玩家分支选择及引人追查的结尾。请实际交付可下载的Markdown人物档案文件，里面包含每个人的背景、动机、秘密和相互关系。",
            f"请承接本轮《{title}》已有的故事世界、{hero}的人物档案、关于「{secret}」的悬念以及上一章的三条分支，不要改写设定。从第一章的结尾续写《{chapter}》，让三种选择各自造成后果，写至少五段可选择的互动对话、两个反转及下章伏笔，明确联系第一章线索和人物档案中的具体信息。",
        ]
        end_anchor = ""
    return {
        "scene": scene, "serial": serial, "title": topic[0],
        "prompts": prompts, "end_anchor": end_anchor, "keywords": ([topic[0], topic[3]] if scene == "D" else [topic[0]]),
        "kind": {"A":"rich_text", "B":"three_images", "C":"four_files", "D":"two_turns"}[scene],
    }


class ClaimInput(BaseModel):
    scene: str = Field(pattern=r"^[ABCD]$")
    claim_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class OneShotPool:
    def __init__(self, data_dir: Path):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.filename = root / "playground_abcd_one_shot.sqlite3"
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS abcd_counter (scene TEXT PRIMARY KEY, serial INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS abcd_claim (claim_id TEXT PRIMARY KEY, scene TEXT NOT NULL, case_json TEXT NOT NULL, claimed_at TEXT NOT NULL)")
            db.commit()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.filename), timeout=15)
        try:
            yield db
        finally:
            db.close()

    def preview(self, scene: str) -> dict:
        if scene not in "ABCD" or len(scene) != 1:
            raise ValueError("Invalid scene")
        with self.connection() as db:
            row = db.execute("SELECT serial FROM abcd_counter WHERE scene=?", (scene,)).fetchone()
        item = build_case(scene, row[0] if row else 0)
        return {"scene": scene, "title": item["title"], "serial": item["serial"],
                "prompt_preview": item["prompts"][0][:260]}

    def claim(self, scene: str, claim_id: str) -> dict:
        # BEGIN IMMEDIATE is the cross-process single decision authority.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT scene,case_json FROM abcd_claim WHERE claim_id=?", (claim_id,)).fetchone()
            if prior:
                if prior[0] != scene:
                    raise ValueError("Claim ID belongs to another scene")
                item = json.loads(prior[1])
            else:
                row = db.execute("SELECT serial FROM abcd_counter WHERE scene=?", (scene,)).fetchone()
                serial = row[0] if row else 0
                item = build_case(scene, serial)
                db.execute("INSERT INTO abcd_counter(scene,serial) VALUES (?,?) ON CONFLICT(scene) DO UPDATE SET serial=excluded.serial",
                           (scene, serial+1))
                db.execute("INSERT INTO abcd_claim(claim_id,scene,case_json,claimed_at) VALUES (?,?,?,?)",
                           (claim_id,scene,json.dumps(item,ensure_ascii=False),
                            datetime.now(timezone.utc).isoformat()))
            db.commit()
        return {**item, "claim_id": claim_id, "next": self.preview(scene)}


def install_playground_abcd_pool(app: FastAPI) -> None:
    if getattr(app.state,"playground_abcd_one_shot_installed",False):
        return
    data_dir = app.state.settings.data_dir
    pool = OneShotPool(data_dir)
    app.state.playground_abcd_one_shot_pool = pool
    app.state.playground_abcd_one_shot_installed = True

    async def admin_token(authorization: str | None = Header(default=None),
                          x_api_key: str | None = Header(default=None)):
        provided = str(x_api_key or "").strip()
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        expected = str(app.state.settings.api_key or "")
        if not expected or not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Administrator API key required")

    from fastapi import Depends

    @app.get("/api/admin/playground/abcd/preview", dependencies=[Depends(admin_token)])
    async def abcd_preview():
        return {"scenes": {scene:pool.preview(scene) for scene in "ABCD"}, "version":POOL_VERSION}

    @app.post("/api/admin/playground/abcd/claim", dependencies=[Depends(admin_token)])
    async def abcd_claim(body: ClaimInput):
        try:
            return pool.claim(body.scene,body.claim_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
