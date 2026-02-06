import streamlit as st
import pandas as pd
import os
import json
import time
import copy
import altair as alt
import streamlit.components.v1 as components
import zipfile
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==========================================
# 0. 系統設定
# ==========================================
st.set_page_config(page_title="多專案施工管理系統 PRO Max (線上版)", layout="wide", page_icon="🏗️")

# --- 🔐 安全設定 ---
SYSTEM_PASSWORD = "12345" 

# --- 檔案路徑 ---
SETTINGS_FILE = 'settings.json'
PRICES_FILE = 'item_prices.json'
KEY_FILE = 'service_key.json'      # Google API 金鑰
SHEET_NAME = 'construction_db'     # Google 試算表名稱

# --- 台灣例假日 ---
HOLIDAYS = {
    "2025-01-01": "元旦", "2025-01-27": "小年夜", "2025-01-28": "除夕", "2025-01-29": "春節", "2025-01-30": "初二", "2025-01-31": "初三",
    "2025-02-28": "和平紀念日", "2025-04-04": "兒童節/清明節", "2025-05-01": "勞動節", "2025-05-31": "端午節",
    "2025-10-06": "中秋節", "2025-10-10": "國慶日",
    "2026-01-01": "元旦", "2026-02-16": "小年夜", "2026-02-17": "除夕", "2026-02-18": "春節",
    "2026-02-28": "和平紀念日", "2026-04-04": "兒童節", "2026-04-05": "清明節", "2026-05-01": "勞動節",
    "2026-06-19": "端午節", "2026-09-25": "中秋節", "2026-10-10": "國慶日"
}

# --- 預設值 ---
DEFAULT_CAT_CONFIG = [
    {"key": "施工說明", "display": "01. 施工說明", "type": "text"},
    {"key": "相關紀錄", "display": "02. 相關紀錄", "type": "text"},
    {"key": "進料管理", "display": "03. 進料管理", "type": "text"},
    {"key": "用料管理", "display": "04. 用料管理", "type": "usage"},
    {"key": "工種 (人力)", "display": "05. 工種 (人力)", "type": "cost"},
    {"key": "機具 (設備)", "display": "06. 機具 (設備)", "type": "cost"}
]

DEFAULT_ITEMS = {
    "施工說明": ["正常施工", "暫停施工", "收尾階段", "驗收缺失改善", "天候不佳"],
    "相關紀錄": ["本日會議", "主管走動", "重要事件紀錄", "工安事項", "會勘紀錄"],
    "進料管理": ["鋼筋進場", "水泥進場", "磁磚進場", "設備進場", "其他材料"],
    "用料管理": ["混凝土 3000psi", "混凝土 2500psi", "CLSM", "級配", "水泥砂漿"],
    "工種 (人力)": ["粗工", "泥作", "水電", "油漆", "木工", "鐵工", "板模", "綁鐵", "打石", "清潔"],
    "機具 (設備)": ["挖土機 (怪手)", "山貓", "吊車", "發電機", "空壓機", "破碎機", "夯實機", "貨車"]
}

# ==========================================
# 1. 🔐 登入驗證
# ==========================================
if 'logged_in' not in st.session_state: st.session_state.logged_in = False

def check_login():
    if st.session_state.password_input == SYSTEM_PASSWORD: st.session_state.logged_in = True
    else: st.error("❌ 密碼錯誤")

if not st.session_state.logged_in:
    st.markdown("## 🔒 系統鎖定")
    st.text_input("請輸入密碼：", type="password", key="password_input", on_change=check_login)
    st.stop()

# ==========================================
# 2. 核心邏輯
# ==========================================
@st.cache_resource
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = None
    if os.path.exists(KEY_FILE):
        try: creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        except: return None
    else:
        try:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
        except: return None
    if not creds: return None
    try:
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except: return None

def get_date_info(date_obj):
    weekdays = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]
    date_str = date_obj.strftime("%Y-%m-%d")
    w_str = weekdays[date_obj.weekday()]
    if date_str in HOLIDAYS: return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    return (f"🔴 {w_str}", True) if date_obj.weekday() >= 5 else (f"{w_str}", False)

def load_json(filepath, default_data):
    if not os.path.exists(filepath):
        save_json(filepath, default_data); return default_data
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default_data

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def save_settings(data): save_json(SETTINGS_FILE, data)

def load_settings():
    data = load_json(SETTINGS_FILE, {"projects": ["預設專案"], "items": {"預設專案": copy.deepcopy(DEFAULT_ITEMS)}, "cat_config": copy.deepcopy(DEFAULT_CAT_CONFIG)})
    if "cat_config" not in data: data["cat_config"] = copy.deepcopy(DEFAULT_CAT_CONFIG); save_settings(data)
    for proj in data["projects"]:
        if proj not in data["items"]: data["items"][proj] = {}
        for cat in data["cat_config"]:
            if cat["key"] not in data["items"][proj]: data["items"][proj][cat["key"]] = []
    return data

def load_prices(): return load_json(PRICES_FILE, {})
def save_prices(data): save_json(PRICES_FILE, data)

def load_data():
    cols = ['日期', '專案', '類別', '名稱', '單位', '數量', '單價', '總價', '備註', '月份']
    sheet = get_google_sheet()
    if not sheet: return pd.DataFrame(columns=cols)
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=cols)
        df = pd.DataFrame(data)
        for col in ['專案', '類別', '名稱', '單位', '備註']: df[col] = df[col].fillna("").astype(str)
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce').dt.date
        df = df.dropna(subset=['日期']) 
        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        for col in ['總價', '數量', '單價']: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame(columns=cols)

def save_dataframe(df):
    sheet = get_google_sheet()
    if not sheet: return
    df_save = df.copy().fillna('') 
    df_save = df_save.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日', '🗓️ 星期/節日'] if c in df_save.columns])
    df_save['日期'] = df_save['日期'].astype(str)
    try:
        sheet.clear(); sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
    except Exception as e: st.error(f"雲端存檔失敗: {e}")

def append_data(date, project, category, category_type, name, unit, qty, price, note):
    total = qty * price if category_type == 'cost' else 0
    row = [str(date), project, category, name, unit, qty, price, total, note]
    sheet = get_google_sheet()
    if sheet: sheet.append_row(row)

def update_by_scope(original_df, edited_part, proj, month, cat_key):
    original_df['temp_month'] = pd.to_datetime(original_df['日期']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['專案'] == proj) & (original_df['類別'] == cat_key)
    df_kept = original_df[~mask].copy()
    edited_clean = edited_part.drop(columns=[c for c in ['刪除', '星期/節日', '🗓️ 星期/節日'] if c in edited_part.columns])
    for col in ['數量', '單價']: edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    cat_type = next((c['type'] for c in CAT_CONFIG_LIST if c['key'] == cat_key), 'text')
    edited_clean['總價'] = edited_clean.apply(lambda r: r['數量']*r['單價'] if cat_type == 'cost' else 0, axis=1)
    return pd.concat([df_kept, edited_clean], ignore_index=True)

def update_item_name(project, category, old_name, new_name, settings, prices):
    if old_name == new_name: return False
    curr_list = settings["items"][project].get(category, [])
    if new_name in curr_list: return False 
    if old_name in curr_list: curr_list[curr_list.index(old_name)] = new_name
    if project in prices and category in prices[project] and old_name in prices[project][category]:
        prices[project][category][new_name] = prices[project][category].pop(old_name)
        save_prices(prices)
    df_cur = load_data()
    if not df_cur.empty:
        df_cur.loc[(df_cur['專案']==project) & (df_cur['類別']==category) & (df_cur['名稱']==old_name), '名稱'] = new_name
        save_dataframe(df_cur)
    save_settings(settings); return True

def update_category_config(idx, new_display, settings):
    settings["cat_config"][idx]["display"] = new_display; save_settings(settings); return True

def add_new_category_block(new_key, new_display, new_type, settings):
    for cat in settings["cat_config"]:
        if cat["key"] == new_key: return False
    settings["cat_config"].append({"key": new_key, "display": new_display, "type": new_type})
    for proj in settings["items"]:
        if new_key not in settings["items"][proj]: settings["items"][proj][new_key] = []
    save_settings(settings); return True

def delete_category_block(idx, settings):
    del settings["cat_config"][idx]; save_settings(settings); return True

def create_zip_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        df_bak = load_data()
        zip_file.writestr("construction_data.csv", df_bak.to_csv(index=False))
        for f in [SETTINGS_FILE, PRICES_FILE]:
            if os.path.exists(f): zip_file.write(f)
    buffer.seek(0); return buffer

# --- 初始化 ---
settings_data = load_settings(); price_data = load_prices(); df = load_data()
CAT_CONFIG_LIST = settings_data["cat_config"]
if 'mem_project' not in st.session_state: st.session_state.mem_project = settings_data["projects"][0]
if 'mem_date' not in st.session_state: st.session_state.mem_date = datetime.now()
if 'last_check_date' not in st.session_state: st.session_state.last_check_date = st.session_state.mem_date

# ==========================================
# 主介面
# ==========================================
st.title("🏗️ 多專案施工管理系統 PRO Max (線上版)")

with st.sidebar:
    st.header("📅 日期與專案設定")
    proj_list = settings_data["projects"]
    if st.session_state.mem_project not in proj_list: st.session_state.mem_project = proj_list[0]
    global_project = st.selectbox("🏗️ 目前專案", proj_list, index=proj_list.index(st.session_state.mem_project))
    global_date = st.date_input("📅 工作日期", st.session_state.mem_date)
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
        components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)
    day_str, is_red = get_date_info(global_date)
    st.markdown(f"### {global_date} {day_str}")
    st.session_state.mem_project = global_project; st.session_state.mem_date = global_date
    current_items = settings_data["items"].get(global_project, {})
    if st.button("🔄 強制重新整理"): st.cache_resource.clear(); st.rerun()
    if st.button("🔒 登出"): st.session_state.logged_in = False; st.rerun()

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "🏗️ 專案管理區"])

# === Tab 1: 快速日報輸入 (修正自動抓取單位與單價) ===
with tab_entry:
    st.info(f"正在填寫：**{global_project}** / **{global_date}**")
    d_key = str(global_date); handled_keys = []

    # 1. 施工說明 & 相關紀錄 (固定前兩項)
    if len(CAT_CONFIG_LIST) >= 2:
        with st.expander(f"📝 {CAT_CONFIG_LIST[0]['display']} 及 {CAT_CONFIG_LIST[1]['display']}", expanded=True):
            cols = st.columns(2)
            for i in range(2):
                conf = CAT_CONFIG_LIST[i]; handled_keys.append(conf["key"])
                with cols[i]:
                    st.markdown(f"**{conf['display']}**")
                    opts = current_items.get(conf["key"], [])
                    it = st.selectbox("項目", opts if opts else ["(請先至設定頁新增項目)"], key=f"s_{i}_{d_key}")
                    p_set = price_data.get(global_project, {}).get(conf["key"], {}).get(it, {"price": 0, "unit": "式"})
                    with st.form(key=f"f_{i}_{d_key}"):
                        tx = st.text_area("內容", height=100, key=f"a_{i}_{d_key}")
                        if st.form_submit_button("💾 儲存") and opts:
                            append_data(global_date, global_project, conf["key"], conf["type"], it, p_set["unit"], 1, 0, tx); st.toast("儲存成功")

    # 2. 進料管理 (固定第三項)
    if len(CAT_CONFIG_LIST) >= 3:
        conf = CAT_CONFIG_LIST[2]; handled_keys.append(conf["key"])
        with st.expander(f"🚛 {conf['display']}", expanded=True):
            cols = st.columns(3); opts = current_items.get(conf["key"], [])
            for k in range(3):
                with cols[k]:
                    it = st.selectbox("材料", opts if opts else ["(請先新增項目)"], key=f"is_{k}_{d_key}")
                    p_set = price_data.get(global_project, {}).get(conf["key"], {}).get(it, {"price": 0, "unit": "式"})
                    with st.form(key=f"f_2_{k}_{d_key}"):
                        q = st.number_input("數量", min_value=0.0, step=1.0, key=f"iq_{k}_{d_key}")
                        u = st.text_input("單位", value=p_set["unit"], key=f"iu_{k}_{d_key}_{it}")
                        if st.form_submit_button(f"💾 儲存 {k+1}") and opts:
                            append_data(global_date, global_project, conf["key"], conf["type"], it, u, q, 0, ""); st.rerun()

    # 3. 用料管理 (固定第四項)
    if len(CAT_CONFIG_LIST) >= 4:
        conf = CAT_CONFIG_LIST[3]; handled_keys.append(conf["key"])
        with st.expander(f"🧱 {conf['display']}", expanded=True):
            cols = st.columns(3); opts = current_items.get(conf["key"], [])
            for k in range(3):
                with cols[k]:
                    it = st.selectbox("材料", opts if opts else ["(請先新增項目)"], key=f"us_{k}_{d_key}")
                    p_set = price_data.get(global_project, {}).get(conf["key"], {}).get(it, {"price": 0, "unit": "m3"})
                    with st.form(key=f"f_3_{k}_{d_key}"):
                        q = st.number_input("數量", min_value=0.0, step=0.5, key=f"uq_{k}_{d_key}")
                        u = st.text_input("單位", value=p_set["unit"], key=f"uu_{k}_{d_key}_{it}")
                        if st.form_submit_button(f"💾 儲存 {k+1}") and opts:
                            append_data(global_date, global_project, conf["key"], conf["type"], it, u, q, 0, ""); st.rerun()

    # 4. 人力與機具 (固定第五、六項)
    if len(CAT_CONFIG_LIST) >= 6:
        with st.expander("👷 人力與機具出工紀錄", expanded=True):
            cols = st.columns(2)
            for i in [4, 5]:
                conf = CAT_CONFIG_LIST[i]; handled_keys.append(conf["key"])
                with cols[i-4]:
                    st.markdown(f"### {conf['display']}")
                    opts = current_items.get(conf["key"], [])
                    it = st.selectbox("項目", opts if opts else ["(請先新增項目)"], key=f"cs_{i}_{d_key}")
                    p_set = price_data.get(global_project, {}).get(conf["key"], {}).get(it, {"price": 0, "unit": "工" if i==4 else "式"})
                    with st.form(key=f"f_{i}_{d_key}"):
                        cq, cp = st.columns(2)
                        q = cq.number_input("數量", value=1.0, step=0.5, key=f"cq_{i}_{d_key}")
                        p = cp.number_input("單價", value=float(p_set["price"]), key=f"cp_{i}_{d_key}_{it}")
                        u = st.text_input("單位", value=p_set["unit"], key=f"cu_{i}_{d_key}_{it}")
                        if st.form_submit_button("💾 新增紀錄") and opts:
                            append_data(global_date, global_project, conf["key"], conf["type"], it, u, q, p, ""); st.rerun()

    # 🌟 動態同步區：自動偵測並顯示你在管理區新增的所有剩餘標題
    for conf in CAT_CONFIG_LIST:
        if conf["key"] not in handled_keys:
            with st.expander(f"📌 {conf['display']}", expanded=True):
                opts = current_items.get(conf["key"], [])
                if opts:
                    it = st.selectbox("選擇項目", opts, key=f"ds_{conf['key']}")
                    p_set = price_data.get(global_project, {}).get(conf["key"], {}).get(it, {"price": 0, "unit": "式"})
                    with st.form(key=f"dyn_{conf['key']}_{d_key}"):
                        if conf["type"] == 'text': tx = st.text_area("內容內容", key=f"dt_{conf['key']}"); q, p, u = 1, 0, p_set["unit"]
                        else:
                            c1, c2, c3 = st.columns(3)
                            q = c1.number_input("數量", value=1.0, key=f"dq_{conf['key']}")
                            p = c2.number_input("單價", value=float(p_set["price"]), key=f"dp_{conf['key']}_{it}") if conf["type"] == 'cost' else 0
                            u = c3.text_input("單位", value=p_set["unit"], key=f"du_{conf['key']}_{it}"); tx = ""
                        if st.form_submit_button("💾 儲存資料"):
                            append_data(global_date, global_project, conf["key"], conf["type"], it, u, q, p, tx); st.rerun()

# === Tab 2: 報表總覽 (修正更新不見問題與數量排序) ===
with tab_data:
    proj_df = df[df['專案'] == global_project].copy()
    if proj_df.empty: st.info(f"專案【{global_project}】無資料")
    else:
        c1, c2, c3 = st.columns([2, 2, 2])
        months = sorted(proj_df['月份'].unique().tolist(), reverse=True)
        with c1: ed_month = st.selectbox("編輯月份", months, key="ed_m")
        month_df = proj_df[proj_df['月份'] == ed_month].copy()
        dates = sorted(month_df['日期'].unique().tolist())
        with c2: ed_date = st.selectbox("日期篩選", ["整個月"] + dates, key="ed_d")
        with c3: search = st.text_input("搜尋關鍵字", key="search_key")
        st.divider()

        def render_section(cat_key, cat_disp, cat_type, key):
            sk = f"conf_{key}"; 
            if sk not in st.session_state: st.session_state[sk] = False
            sec_df = month_df[month_df['類別'] == cat_key].copy()
            if not sec_df.empty:
                st.subheader(cat_disp)
                view = sec_df.copy()
                if ed_date != "整個月": view = view[view['日期'] == ed_date]
                if search:
                    mask = view.apply(lambda x: search in str(x['名稱']) or search in str(x['備註']), axis=1)
                    view = view[mask]
                
                if not view.empty:
                    view['🗓️ 星期/節日'] = view['日期'].apply(lambda x: get_date_info(x)[0])
                    if '刪除' not in view.columns: view.insert(0, "刪除", False)
                    
                    # 依類型過濾與排序欄位
                    if cat_disp.startswith("01.") or cat_disp.startswith("02."):
                        cols_to_show = ['刪除', '日期', '🗓️ 星期/節日', '名稱', '備註']
                    elif cat_disp.startswith("03.") or cat_disp.startswith("04.") or cat_type == 'usage':
                        # 數量類：單位之後增加數量
                        cols_to_show = ['刪除', '日期', '🗓️ 星期/節日', '名稱', '單位', '數量', '備註']
                    else:
                        cols_to_show = ['刪除', '日期', '🗓️ 星期/節日', '名稱', '數量', '單位', '單價', '總價', '備註']
                    
                    view_final = view[[c for c in cols_to_show if c in view.columns]]
                    col_cfg = {"刪除": st.column_config.CheckboxColumn(width="small"), "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"), "🗓️ 星期/節日": st.column_config.TextColumn(disabled=True, width="medium"), "名稱": st.column_config.TextColumn(width="medium"), "數量": st.column_config.NumberColumn(width="small"), "單位": st.column_config.TextColumn(width="small"), "單價": st.column_config.NumberColumn(width="small"), "總價": st.column_config.NumberColumn(disabled=True, width="small"), "備註": st.column_config.TextColumn(width="large")}
                    edited = st.data_editor(view_final.sort_values('日期', ascending=False), key=f"e_{key}", column_config=col_cfg, use_container_width=True, hide_index=True)
                    
                    b1, b2, _ = st.columns([1, 1, 6])
                    with b1: 
                        if st.button("💾 更新修改", key=f"s_{key}"): 
                            target_indices = edited.index
                            common_cols = [c for c in edited.columns if c in df.columns and c not in ['刪除', '🗓️ 星期/節日']]
                            for col in common_cols: df.loc[target_indices, col] = edited[col]
                            if cat_type == 'cost': df.loc[target_indices, '總價'] = df.loc[target_indices, '數量'] * df.loc[target_indices, '單價']
                            if '刪除' in edited.columns:
                                delete_indices = edited[edited['刪除']].index
                                if not delete_indices.empty: df.drop(delete_indices, inplace=True)
                            save_dataframe(df); st.toast("✅ 更新成功"); time.sleep(0.5); st.rerun()

                    with b2: 
                        if st.button("🗑️ 刪除選取", key=f"d_{key}", type="primary"): 
                            if not edited[edited['刪除']].empty: st.session_state[sk] = True
                    if st.session_state[sk]: 
                        st.warning("確定刪除？")
                        if st.button("✔️ 是", key=f"y_{key}"):
                            delete_indices = edited[edited['刪除']].index
                            df.drop(delete_indices, inplace=True); save_dataframe(df); st.session_state[sk] = False; st.rerun()
                        if st.button("❌ 否", key=f"n_{key}"): st.session_state[sk] = False; st.rerun()

        for config in CAT_CONFIG_LIST:
            render_section(config["key"], config["display"], config["type"], f"sec_{config['key']}")

# === Tab 3: 成本儀表板 (年月選單連動) ===
with tab_dash:
    if not df.empty:
        dash_df = df[df['專案'] == global_project].copy()
        if not dash_df.empty:
            dash_df['Year'] = pd.to_datetime(dash_df['日期']).dt.year
            y_list = sorted(dash_df['Year'].unique().tolist(), reverse=True)
            c_y, c_m, _ = st.columns([2, 2, 4])
            with c_y: sel_y = st.selectbox("📅 統計年份", y_list, key="dash_y")
            year_df = dash_df[dash_df['Year'] == sel_y]
            m_list = sorted(year_df['月份'].unique().tolist(), reverse=True)
            with c_m: sel_m = st.selectbox("📅 統計月份", m_list, key="dash_m")
            month_df = year_df[year_df['月份'] == sel_m]; today_str = datetime.now().date()
            k1, k2, k3 = st.columns(3)
            k1.metric("今日費用", f"${dash_df[dash_df['日期'] == today_str]['總價'].sum():,.0f}")
            k2.metric(f"{sel_m} 費用", f"${month_df['總價'].sum():,.0f}")
            k3.metric(f"{sel_y} 年度總計", f"${year_df['總價'].sum():,.0f}")
            st.divider()
            cost_df = month_df[month_df['總價'] > 0]
            if not cost_df.empty:
                st.altair_chart(alt.Chart(cost_df.groupby('類別')['總價'].sum().reset_index()).mark_arc(outerRadius=100, innerRadius=50).encode(theta="總價", color="類別", tooltip=["類別", "總價"]), use_container_width=True)
                for c in cost_df['類別'].unique():
                    c_data = cost_df[cost_df['類別'] == c]
                    with st.expander(f"{c} (總計: ${c_data['總價'].sum():,.0f})"):
                        st.bar_chart(c_data.groupby('名稱')['總價'].sum().reset_index().sort_values('總價', ascending=False), x='名稱', y='總價')
            else: st.info(f"{sel_m} 尚無金額紀錄。")

# === Tab 4: 🏗️ 專案管理區 (強化還原與語法修正) ===
with tab_settings:
    st.header("🏗️ 專案管理區")
    with st.expander("📦 資料備份中心", expanded=False):
        st.download_button("📦 下載完整備份 (ZIP)", create_zip_backup(), file_name=f"backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
        uploaded_file = st.file_uploader("📤 系統還原 (ZIP/CSV/JSON)", type=['csv', 'zip', 'json'])
        if uploaded_file and st.button("⚠️ 確認執行還原"):
            try:
                if uploaded_file.name.endswith('.json'):
                    target_path = SETTINGS_FILE if "settings" in uploaded_file.name else PRICES_FILE
                    with open(target_path, 'wb') as f: f.write(uploaded_file.getbuffer())
                    st.success(f"設定檔還原成功！"); time.sleep(1); st.rerun()
                elif uploaded_file.name.endswith('.csv'):
                    df_new = pd.read_csv(uploaded_file, encoding='utf-8-sig'); save_dataframe(df_new)
                    new_projs = df_new['專案'].unique().tolist(); changed = False
                    for p in new_projs:
                        if p and p not in settings_data["projects"]:
                            settings_data["projects"].append(p)
                            if p not in settings_data["items"]: settings_data["items"][p] = copy.deepcopy(DEFAULT_ITEMS)
                            changed = True
                    if changed: save_settings(settings_data)
                    st.success("資料還原成功！"); time.sleep(1); st.rerun()
                elif uploaded_file.name.endswith('.zip'):
                    with zipfile.ZipFile(uploaded_file, 'r') as z: z.extractall(".")
                    st.success("全環境還原成功！"); time.sleep(1); st.rerun()
            except Exception as e: st.error(f"還原失敗：{e}")

    with st.expander("1. 專案管理", expanded=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            np_in = st.text_input("新增專案名稱")
            if st.button("➕ 新增專案") and np_in:
                settings_data["projects"].append(np_in); settings_data["items"][np_in] = copy.deepcopy(DEFAULT_ITEMS); save_settings(settings_data); st.rerun()
        with c2:
            rp_in = st.text_input("修改名稱為", value=global_project)
            if st.button("✏️ 確認改名") and rp_in != global_project:
                settings_data["projects"][settings_data["projects"].index(global_project)] = rp_in
                settings_data["items"][rp_in] = settings_data["items"].pop(global_project); save_settings(settings_data); st.rerun()
        with c3:
            if len(proj_list) > 1 and st.button("🗑️ 刪除專案", type="primary"):
                settings_data["projects"].remove(global_project); save_settings(settings_data); st.rerun()

    st.divider(); st.subheader("📋 選單項目管理")
    with st.expander("1. 從其他專案匯入選單範本", expanded=False):
        others = [p for p in proj_list if p != global_project]
        if others:
            src_p = st.selectbox("選擇來源專案", others)
            if "imp_state" not in st.session_state: st.session_state.imp_state = False
            if not st.session_state.imp_state:
                if st.button("📥 匯入", type="primary"): st.session_state.imp_state = True; st.rerun()
            else:
                st.warning("確定匯入？")
                if st.button("是", key="y_i"):
                    for k, v in settings_data["items"][src_p].items():
                        if k not in current_items: current_items[k] = []
                        for it_m in v:
                            if it_m not in current_items[k]: current_items[k].append(it_m)
                    save_settings(settings_data); st.session_state.imp_state = False; st.rerun()
                if st.button("否", key="n_i"): st.session_state.imp_state = False; st.rerun()
    
    with st.expander("2. 新增管理項目 (新增大標題)", expanded=False):
        c1, c2, c3 = st.columns([2, 2, 1])
        n_bn = c1.text_input("大標題名稱 (如: 07.安全檢查)")
        n_bt = c2.selectbox("類型", ["text", "usage", "cost"], format_func=lambda x: {"text":"文字","usage":"數量","cost":"成本"}[x])
        if c3.button("新增標題") and n_bn:
            nk = n_bn.split('.')[-1].strip(); add_new_category_block(nk, n_bn, n_bt, settings_data); st.rerun()

    with st.expander("3. 既有選單項目管理 (修改大標題 / 細項內容)", expanded=True):
        st.markdown("##### 修改大標題名稱")
        for i, conf in enumerate(CAT_CONFIG_LIST):
            c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
            c1.text(f"原: {conf['display']}")
            nd_in = c2.text_input(f"新標題 {i}", value=conf['display'], label_visibility="collapsed")
            if nd_in != conf['display'] and st.button("更新", key=f"u_{i}"): update_category_config(i, nd_in, settings_data); st.rerun()
            if c4.button("🗑️", key=f"d_{i}"): delete_category_block(i, settings_data); st.rerun()
        st.markdown("---"); st.markdown("##### 管理項目細項內容")
        target_v = st.selectbox("選擇類別", [c["display"] for c in CAT_CONFIG_LIST])
        t_conf = next((c for c in CAT_CONFIG_LIST if c["display"] == target_v), None)
        if t_conf:
            tk = t_conf["key"]; ct = t_conf["type"]; c_list = current_items.get(tk, [])
            c_a, c_b = st.columns([3, 1])
            ni_in = c_a.text_input(f"在【{target_v}】新增項目內容", key=f"no_{tk}")
            if c_b.button("➕ 加入項目", key=f"ba_{tk}") and ni_in: current_items[tk].append(ni_in); save_settings(settings_data); st.rerun()
            st.markdown(f"**目前項目清單 ({len(c_list)})**")
            if ct == 'text': h1, h2, h3, h4 = st.columns([3, 3, 1, 1]); h1.caption("原名稱"); h2.caption("新名稱"); h3.caption("存"); h4.caption("刪")
            elif ct == 'usage': h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 1, 1]); h1.caption("原名稱"); h2.caption("新名稱"); h3.caption("預設單位"); h4.caption("存"); h5.caption("刪")
            else: h1, h2, h3, h4, h5, h6 = st.columns([2, 2, 1, 1, 0.5, 0.5]); h1.caption("原名稱"); h2.caption("新名稱"); h3.caption("單價"); h4.caption("單位"); h5.caption("存"); h6.caption("刪")
            for it_v in c_list:
                p_i = price_data.get(global_project, {}).get(tk, {}).get(it_v, {"price": 0, "unit": "式"})
                if ct == 'text':
                    r1, r2, r3, r4 = st.columns([3, 3, 1, 1])
                    with r1: st.text(it_v)
                    with r2: rnn_in = r2.text_input("RN", value=it_v, key=f"r_{tk}_{it_v}", label_visibility="collapsed")
                    if r3.button("💾", key=f"s_{tk}_{it_v}"):
                        if rnn_in != it_v: update_item_name(global_project, tk, it_v, rnn_in, settings_data, price_data)
                        st.toast("已更新"); st.rerun()
                    if r4.button("🗑️", key=f"dl_{tk}_{it_v}"): current_items[tk].remove(it_v); save_settings(settings_data); st.rerun()
                elif ct == 'usage':
                    r1, r2, r3, r4, r5 = st.columns([2, 2, 2, 1, 1])
                    with r1: st.text(it_v)
                    with r2: rnn_in = r2.text_input("RN", value=it_v, key=f"r_{tk}_{it_v}", label_visibility="collapsed")
                    with r3: nu_in = r3.text_input("U", value=p_i["unit"], key=f"u_{tk}_{it_v}", label_visibility="collapsed")
                    if r4.button("💾", key=f"s_{tk}_{it_v}"):
                        if rnn_in != it_v: update_item_name(global_project, tk, it_v, rnn_in, settings_data, price_data)
                        if tk not in price_data[global_project]: price_data[global_project][tk] = {}
                        price_data[global_project][tk][rnn_in if rnn_in != it_v else it_v] = {"price": 0, "unit": nu_in}; save_prices(price_data); st.rerun()
                    if r5.button("🗑️", key=f"dl_{tk}_{it_v}"): current_items[tk].remove(it_v); save_settings(settings_data); st.rerun()
                else:
                    r1, r2, r3, r4, r5, r6 = st.columns([2, 2, 1, 1, 0.5, 0.5])
                    with r1: st.text(it_v)
                    with r2: rnn_in = r2.text_input("RN", value=it_v, key=f"r_{tk}_{it_v}", label_visibility="collapsed")
                    with r3: np_in = r3.number_input("P", value=float(p_i["price"]), key=f"p_{tk}_{it_v}", label_visibility="collapsed")
                    with r4: nu_in = r4.text_input("U", value=p_i["unit"], key=f"u_{tk}_{it_v}", label_visibility="collapsed")
                    if r5.button("💾", key=f"s_{tk}_{it_v}"):
                        if rnn_in != it_v: update_item_name(global_project, tk, it_v, rnn_in, settings_data, price_data)
                        if tk not in price_data[global_project]: price_data[global_project][tk] = {}
                        price_data[global_project][tk][rnn_in if rnn_in != it_v else it_v] = {"price": np_in, "unit": nu_in}; save_prices(price_data); st.rerun()
                    if r6.button("🗑️", key=f"dl_{tk}_{it_v}"): current_items[tk].remove(it_v); save_settings(settings_data); st.rerun()