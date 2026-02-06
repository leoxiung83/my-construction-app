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

# --- 檔案路徑 (本地設定檔) ---
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
# 2. 核心邏輯 (Google Sheets & JSON)
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
    is_weekend = date_obj.weekday() >= 5
    if date_str in HOLIDAYS: return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    if is_weekend: return f"🔴 {w_str}", True 
    return f"{w_str}", False

def load_json(filepath, default_data):
    if not os.path.exists(filepath):
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default_data

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def save_settings(data): save_json(SETTINGS_FILE, data)

def load_settings():
    data = load_json(SETTINGS_FILE, {"projects": ["預設專案"], "items": {"預設專案": copy.deepcopy(DEFAULT_ITEMS)}, "cat_config": copy.deepcopy(DEFAULT_CAT_CONFIG)})
    if "cat_config" not in data:
        data["cat_config"] = copy.deepcopy(DEFAULT_CAT_CONFIG); save_settings(data)
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
        # 補齊欄位
        for c in cols: 
            if c not in df.columns: df[c] = ""
        
        # 補強: 確保字串欄位為字串，避免 None/NaN
        for col in ['專案', '類別', '名稱', '單位', '備註']:
            df[col] = df[col].fillna("").astype(str)

        # 關鍵修正: 轉換日期並移除無效日期 (NaN/NaT)
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce').dt.date
        df = df.dropna(subset=['日期']) 

        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        for col in ['總價', '數量', '單價']: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame(columns=cols)

def save_dataframe(df):
    sheet = get_google_sheet()
    if not sheet: return
    df_save = df.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日'] if c in df.columns])
    df_save['日期'] = df_save['日期'].astype(str)
    try:
        sheet.clear()
        sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
    except Exception as e: st.error(f"雲端存檔失敗: {e}")

def append_data(date, project, category, category_type, name, unit, qty, price, note):
    total = 0
    if category_type == 'cost': total = qty * price
    
    row = [str(date), project, category, name, unit, qty, price, total, note]
    sheet = get_google_sheet()
    if sheet: sheet.append_row(row)

def update_by_scope(original_df, edited_part, proj, month, cat_key):
    original_df['temp_month'] = pd.to_datetime(original_df['日期']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['專案'] == proj) & (original_df['類別'] == cat_key)
    df_kept = original_df[~mask].copy()
    edited_clean = edited_part.drop(columns=[c for c in ['刪除', '星期/節日'] if c in edited_part.columns])
    for col in ['數量', '單價']: edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    
    cat_type = next((c['type'] for c in CAT_CONFIG_LIST if c['key'] == cat_key), 'text')
    def calc_total(row):
        return row['數量'] * row['單價'] if cat_type == 'cost' else 0

    edited_clean['總價'] = edited_clean.apply(calc_total, axis=1)
    return pd.concat([df_kept, edited_clean], ignore_index=True)

def update_item_name(project, category, old_name, new_name, settings, prices):
    if old_name == new_name: return False
    curr_list = settings["items"][project].get(category, [])
    if new_name in curr_list: return False 
    if old_name in curr_list: curr_list[curr_list.index(old_name)] = new_name
    
    if project in prices and category in prices[project] and old_name in prices[project][category]:
        prices[project][category][new_name] = prices[project][category].pop(old_name)
        save_prices(prices)
    
    df = load_data()
    if not df.empty:
        df.loc[(df['專案']==project) & (df['類別']==category) & (df['名稱']==old_name), '名稱'] = new_name
        save_dataframe(df)
    save_settings(settings); return True

def update_category_config(idx, new_display, settings):
    settings["cat_config"][idx]["display"] = new_display
    save_settings(settings); return True

def add_new_category_block(new_key, new_display, new_type, settings):
    for cat in settings["cat_config"]:
        if cat["key"] == new_key: return False
    settings["cat_config"].append({"key": new_key, "display": new_display, "type": new_type})
    for proj in settings["items"]:
        if new_key not in settings["items"][proj]: settings["items"][proj][new_key] = []
    save_settings(settings); return True

def delete_category_block(idx, settings):
    del settings["cat_config"][idx]
    save_settings(settings); return True

def create_zip_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # 備份雲端資料為 CSV
        df = load_data()
        csv_buffer = io.StringIO()
        df.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日'] if c in df.columns]).to_csv(csv_buffer, index=False)
        zip_file.writestr("construction_data.csv", csv_buffer.getvalue())
        # 備份設定檔
        for file in [SETTINGS_FILE, PRICES_FILE]:
            if os.path.exists(file): zip_file.write(file)
    buffer.seek(0)
    return buffer

# --- 初始化 ---
settings_data = load_settings()
price_data = load_prices()
df = load_data()

CAT_CONFIG_LIST = settings_data["cat_config"]
CAT_TYPE_MAP = {c["key"]: c["type"] for c in CAT_CONFIG_LIST}

if 'mem_project' not in st.session_state: st.session_state.mem_project = settings_data["projects"][0] if settings_data["projects"] else "預設專案"
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
    
    global_project = st.selectbox("🏗️ 目前專案", proj_list, index=proj_list.index(st.session_state.mem_project), key="global_proj")
    global_date = st.date_input("📅 工作日期", st.session_state.mem_date, key="global_date")
    
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
        components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)

    day_str, is_red_day = get_date_info(global_date)
    if is_red_day: st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
    else: st.markdown(f"### {global_date} {day_str}")

    st.session_state.mem_project = global_project
    st.session_state.mem_date = global_date
    if global_project not in settings_data["items"]: settings_data["items"][global_project] = {}
    current_items = settings_data["items"][global_project]
    
    if st.button("🔄 強制重新整理"): st.cache_resource.clear(); st.rerun()
    if st.button("🔒 登出"): st.session_state.logged_in = False; st.rerun()

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "🏗️ 專案管理區"])

# === Tab 1: 快速日報輸入 ===
with tab_entry:
    st.info(f"正在填寫：**{global_project}** / **{global_date} {day_str}**")
    d_key = str(global_date)
    configs = CAT_CONFIG_LIST 
    
    # --- 區塊 1: 施工說明 & 相關紀錄 ---
    if len(configs) > 1:
        with st.expander(f"📝 {configs[0]['display']} 及 {configs[1]['display']}", expanded=True):
            cols_g1 = st.columns(2)
            for i in range(2):
                conf = configs[i]
                with cols_g1[i]:
                    st.markdown(f"**{i+1}. {conf['display']}**")
                    with st.form(key=f"form_{i}_{d_key}"):
                        options = current_items.get(conf["key"], [])
                        txt_item = st.selectbox("項目", options if options else ["(請至設定新增)"], key=f"sel_{i}_{d_key}")
                        txt_content = st.text_area("內容", height=100, key=f"area_{i}_{d_key}")
                        if st.form_submit_button("💾 儲存"):
                            append_data(global_date, global_project, conf["key"], conf["type"], txt_item, "式", 1, 0, txt_content)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # --- 區塊 2: 進料 (3欄) ---
    if len(configs) > 2:
        conf = configs[2]; idx = 2
        with st.expander(f"🚛 {conf['display']}", expanded=True):
            cols_g2 = st.columns(3)
            for k in range(3):
                with cols_g2[k]:
                    with st.form(key=f"form_{idx}_{k}_{d_key}"):
                        options = current_items.get(conf["key"], [])
                        in_item = st.selectbox("材料名稱", options if options else ["(請至設定新增)"], key=f"in_sel_{k}_{d_key}")
                        c_q, c_u = st.columns(2)
                        with c_q: in_qty = st.number_input("數量", min_value=0.0, step=1.0, key=f"in_q_{k}_{d_key}")
                        with c_u: in_unit = st.text_input("單位", value="式", key=f"in_u_{k}_{d_key}")
                        in_note = st.text_input("備註", key=f"in_n_{k}_{d_key}")
                        if st.form_submit_button(f"💾 儲存 {k+1}"):
                            append_data(global_date, global_project, conf["key"], conf["type"], in_item, in_unit, in_qty, 0, in_note)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # --- 區塊 3: 用料 (3欄) ---
    if len(configs) > 3:
        conf = configs[3]; idx = 3
        with st.expander(f"🧱 {conf['display']}", expanded=True):
            cols_g3 = st.columns(3)
            for k in range(3):
                with cols_g3[k]:
                    with st.form(key=f"form_{idx}_{k}_{d_key}"):
                        options = current_items.get(conf["key"], [])
                        use_item = st.selectbox("材料名稱", options if options else ["(請至設定新增)"], key=f"use_sel_{k}_{d_key}")
                        c_q, c_u = st.columns(2)
                        with c_q: use_qty = st.number_input("數量", min_value=0.0, step=0.5, key=f"use_q_{k}_{d_key}")
                        with c_u: use_unit = st.text_input("單位", value="m3", key=f"use_u_{k}_{d_key}")
                        use_note = st.text_input("備註", key=f"use_n_{k}_{d_key}")
                        if st.form_submit_button(f"💾 儲存 {k+1}"):
                            append_data(global_date, global_project, conf["key"], conf["type"], use_item, use_unit, use_qty, 0, use_note)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # --- 區塊 4: 人力與機具 (並排) ---
    if len(configs) > 5:
        with st.expander("👷 人力與機具出工紀錄", expanded=True):
            cols_g4 = st.columns(2)
            # 4. 人力 (index 4)
            with cols_g4[0]:
                conf = configs[4]; idx = 4
                st.markdown(f"### {conf['display']}")
                proj_prices = price_data.get(global_project, {}).get(conf["key"], {})
                options = current_items.get(conf["key"], [])
                cost_item = st.selectbox("項目", options if options else ["(請至設定新增)"], key=f"sel_{idx}_{d_key}")
                item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "工"})
                
                with st.form(key=f"f_{idx}_{d_key}"):
                    c_q, c_p = st.columns(2)
                    with c_q: cost_qty = st.number_input("數量", min_value=0.0, step=0.5, value=1.0)
                    with c_p: cost_price = st.number_input("單價", value=float(item_setting["price"]), step=100.0)
                    cost_unit = st.text_input("單位", value=item_setting["unit"])
                    cost_note = st.text_input("備註")
                    if st.form_submit_button("💾 新增紀錄"):
                        append_data(global_date, global_project, conf["key"], conf["type"], cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("已儲存"); time.sleep(0.5); st.rerun()

            # 5. 機具 (index 5)
            with cols_g4[1]:
                conf = configs[5]; idx = 5
                st.markdown(f"### {conf['display']}")
                proj_prices = price_data.get(global_project, {}).get(conf["key"], {})
                options = current_items.get(conf["key"], [])
                cost_item = st.selectbox("項目", options if options else ["(請至設定新增)"], key=f"sel_{idx}_{d_key}")
                item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "式"})
                
                with st.form(key=f"f_{idx}_{d_key}"):
                    c_q, c_p = st.columns(2)
                    with c_q: cost_qty = st.number_input("數量", min_value=0.0, step=0.5, value=1.0)
                    with c_p: cost_price = st.number_input("單價", value=float(item_setting["price"]), step=100.0)
                    cost_unit = st.text_input("單位", value=item_setting["unit"])
                    cost_note = st.text_input("備註")
                    if st.form_submit_button("💾 新增紀錄"):
                        append_data(global_date, global_project, conf["key"], conf["type"], cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # --- 區塊 5: 自訂區塊 ---
    if len(configs) > 6:
        st.divider()
        st.markdown("#### ➕ 其他自訂區塊")
        for i in range(6, len(configs)):
            conf = configs[i]
            with st.expander(f"📝 {conf['display']}", expanded=True):
                with st.form(key=f"form_{i}_{d_key}"):
                    options = current_items.get(conf["key"], [])
                    c1, c2 = st.columns([1, 2])
                    with c1: txt_item = st.selectbox("項目", options if options else ["(請至設定新增)"], key=f"sel_{i}_{d_key}")
                    with c2: 
                        val_unit, val_qty, val_price = "式", 1, 0
                        if conf["type"] == 'text':
                            txt_val = st.text_area("內容", height=68, key=f"val_{i}_{d_key}")
                        elif conf["type"] == 'usage':
                            c_q, c_u = st.columns(2)
                            with c_q: val_qty = st.number_input("數量", min_value=0.0, step=0.5, key=f"qty_{i}_{d_key}")
                            with c_u: val_unit = st.text_input("單位", value="式", key=f"unit_{i}_{d_key}")
                            txt_val = st.text_input("備註", key=f"val_{i}_{d_key}")
                        else:
                            c_q, c_p = st.columns(2)
                            with c_q: val_qty = st.number_input("數量", value=1.0, step=0.5, key=f"qty_{i}_{d_key}")
                            with c_p: val_price = st.number_input("單價", value=0, step=100, key=f"price_{i}_{d_key}")
                            val_unit = st.text_input("單位", value="式", key=f"unit_{i}_{d_key}")
                            txt_val = st.text_input("備註", key=f"val_{i}_{d_key}")
                    
                    if st.form_submit_button("💾 儲存"):
                        append_data(global_date, global_project, conf["key"], conf["type"], txt_item, val_unit, val_qty, val_price, txt_val)
                        st.toast("已儲存"); time.sleep(0.5); st.rerun()

# === Tab 2: 報表總覽 ===
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
                if search: mask = view.apply(lambda x: search in str(x['名稱']) or search in str(x['備註']), axis=1); view = view[mask]
                if not view.empty:
                    view['🗓️ 星期/節日'] = view['日期'].apply(lambda x: get_date_info(x)[0])
                    cols = list(view.columns); cols.insert(1, cols.pop(cols.index('🗓️ 星期/節日')))
                    view = view[cols]
                    hidden = sec_df[~sec_df.index.isin(view.index)]
                    if '刪除' not in view.columns: view.insert(0, "刪除", False)
                    
                    col_cfg = {
                        "刪除": st.column_config.CheckboxColumn(width="small"),
                        "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"),
                        "🗓️ 星期/節日": st.column_config.TextColumn(disabled=True, width="medium"),
                        "名稱": st.column_config.TextColumn(width="medium"),
                        "備註": st.column_config.TextColumn(width="large"),
                        "月份": None, "類別": None, "專案": None
                    }
                    if cat_type == 'cost': col_cfg.update({"單價": st.column_config.NumberColumn(width="small"), "總價": st.column_config.NumberColumn(disabled=True, width="small")})
                    else: col_cfg.update({"單價": None, "總價": None})
                    if cat_type == 'text': col_cfg.update({"數量": None, "單位": None})
                    else: col_cfg.update({"數量": st.column_config.NumberColumn(width="small"), "單位": st.column_config.TextColumn(width="small")})

                    edited = st.data_editor(view.sort_values('日期', ascending=False), key=f"e_{key}", column_config=col_cfg, use_container_width=True, hide_index=True)

                    b1, b2, _ = st.columns([1, 1, 6])
                    with b1: 
                        if st.button("💾 更新修改", key=f"s_{key}"): 
                            merged = pd.concat([hidden, edited.drop(columns=['刪除'])], ignore_index=True)
                            final = update_by_scope(df, merged, global_project, ed_month, cat_key)
                            save_dataframe(final); st.success("已更新"); time.sleep(0.5); st.rerun()
                    with b2: 
                        if st.button("🗑️ 刪除選取", key=f"d_{key}", type="primary"): 
                            if not edited[edited['刪除']].empty: st.session_state[sk] = True
                    if st.session_state[sk]: 
                        st.warning("確定刪除？")
                        if st.button("✔️ 是", key=f"y_{key}"):
                            vis = edited[~edited['刪除']].drop(columns=['刪除'])
                            final = update_by_scope(df, vis, global_project, ed_month, cat_key)
                            save_dataframe(final); st.session_state[sk] = False; st.rerun()
                        if st.button("❌ 否", key=f"n_{key}"): st.session_state[sk] = False; st.rerun()

        for config in CAT_CONFIG_LIST:
            render_section(config["key"], config["display"], config["type"], f"sec_{config['key']}")

# === Tab 3: 成本儀表板 ===
with tab_dash:
    if df.empty: st.info("無資料")
    else:
        dash_df = df[df['專案'] == global_project].copy()
        if dash_df.empty: st.warning(f"專案【{global_project}】目前沒有資料。")
        else:
            dash_df['Year'] = pd.to_datetime(dash_df['日期']).dt.year
            all_years = sorted(dash_df['Year'].unique().tolist(), reverse=True)
            c_year_sel, _ = st.columns([1, 3])
            with c_year_sel:
                current_year = datetime.now().year
                default_idx = all_years.index(current_year) if current_year in all_years else 0
                sel_year = st.selectbox("📅 統計年份", all_years, index=default_idx, key="dash_year_sel")

            today_str = datetime.now().date(); cur_month = today_str.strftime("%Y-%m")
            d_cost = dash_df[dash_df['日期'] == today_str]['總價'].sum()
            m_cost = dash_df[dash_df['月份'] == cur_month]['總價'].sum()
            y_cost = dash_df[dash_df['Year'] == sel_year]['總價'].sum()
            t_cost = dash_df['總價'].sum()
            
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("今日費用", f"${d_cost:,.0f}")
            k2.metric("本月費用", f"${m_cost:,.0f}")
            k3.metric(f"{sel_year}年費用", f"${y_cost:,.0f}")
            k4.metric("專案總費用", f"${t_cost:,.0f}")
            st.divider()
            
            cost_df = dash_df[(dash_df['總價'] > 0) & (dash_df['Year'] == sel_year)]
            if not cost_df.empty:
                months = sorted(cost_df['月份'].unique().tolist(), reverse=True)
                c_sel, _ = st.columns([1,3])
                with c_sel: sel_chart_m = st.selectbox("圖表統計月份", months)
                chart_data = cost_df[cost_df['月份'] == sel_chart_m].copy()
                if not chart_data.empty:
                    st.subheader(f"💰 {sel_chart_m} 成本總覽")
                    pie_data = chart_data.groupby('類別')['總價'].sum().reset_index()
                    base = alt.Chart(pie_data).encode(theta=alt.Theta("總價", stack=True))
                    pie = base.mark_arc(outerRadius=100, innerRadius=50).encode(color=alt.Color("類別"), order=alt.Order("總價", sort="descending"), tooltip=["類別", "總價"])
                    st.altair_chart(pie, use_container_width=True)
                    st.markdown("#### 📋 費用明細 (Top 5)")
                    cost_cats = chart_data['類別'].unique()
                    for c in cost_cats:
                        c_data = chart_data[chart_data['類別'] == c]
                        c_sum = c_data['總價'].sum()
                        with st.expander(f"{c} (總計: ${c_sum:,.0f})"):
                            c_bar = c_data.groupby('名稱')['總價'].sum().reset_index().sort_values('總價', ascending=False).head(5)
                            st.bar_chart(c_bar, x='名稱', y='總價')
                            st.dataframe(c_data[['日期', '名稱', '數量', '單價', '總價']], use_container_width=True, hide_index=True)
                else: st.info("此月份無費用資料")
            else: st.info(f"{sel_year} 年尚無金額紀錄。")

# === Tab 4: 專案管理區 (重構排版) ===
with tab_settings:
    st.header("🏗️ 專案管理區")
    
    # 1. 資料備份中心
    with st.expander("📦 資料備份中心", expanded=False):
        st.markdown("此功能會備份雲端資料 (CSV) 與本地設定檔 (JSON)。")
        st.download_button("📦 下載完整備份 (ZIP)", create_zip_backup(), file_name=f"full_backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
        uploaded_file = st.file_uploader("📤 系統還原 (ZIP/CSV)", type=['csv', 'zip'])
        if uploaded_file and st.button("⚠️ 確認還原"):
            try:
                if uploaded_file.name.endswith('.csv'):
                    save_dataframe(pd.read_csv(uploaded_file))
                    st.success("CSV 還原成功！"); time.sleep(1); st.rerun()
                elif uploaded_file.name.endswith('.zip'):
                    with zipfile.ZipFile(uploaded_file, 'r') as z: z.extractall(".")
                    st.success("系統完整還原成功！"); time.sleep(1); st.rerun()
            except Exception as e: st.error(f"還原失敗：{e}")

    # 2. 專案管理
    with st.expander("1. 專案管理", expanded=True):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            new_p = st.text_input("新增專案名稱")
            if st.button("➕ 新增專案"):
                if new_p and new_p not in settings_data["projects"]: 
                    settings_data["projects"].append(new_p)
                    settings_data["items"][new_p] = {}
                    for config in CAT_CONFIG_LIST: settings_data["items"][new_p][config["key"]] = DEFAULT_ITEMS.get(config["key"], [])
                    save_settings(settings_data); st.rerun()
        with c2:
            ren_p = st.text_input("修改目前專案名稱", value=global_project)
            if st.button("✏️ 確認改名"):
                if ren_p != global_project:
                    idx = settings_data["projects"].index(global_project)
                    settings_data["projects"][idx] = ren_p
                    settings_data["items"][ren_p] = settings_data["items"].pop(global_project)
                    if global_project in price_data: price_data[ren_p] = price_data.pop(global_project); save_prices(price_data)
                    save_settings(settings_data)
                    if not df.empty:
                        df.loc[df['專案'] == global_project, '專案'] = ren_p
                        save_dataframe(df)
                    st.session_state.mem_project = ren_p
                    st.success("改名成功"); time.sleep(1); st.rerun()
        
        with c3:
            st.write(""); st.write("") 
            del_proj_key = "del_proj_verify"
            if del_proj_key not in st.session_state: st.session_state[del_proj_key] = False
            if not st.session_state[del_proj_key]:
                if len(settings_data["projects"]) <= 1: st.button("🚫", disabled=True, help="無法刪除")
                else:
                    if st.button("🗑️ 刪除專案", type="primary"): st.session_state[del_proj_key] = True; st.rerun()
            else:
                st.warning("確定刪除？")
                dy, dn = st.columns(2)
                with dy:
                    if st.button("是", key="confirm_del_proj"):
                        settings_data["projects"].remove(global_project)
                        del settings_data["items"][global_project]
                        if global_project in price_data: del price_data[global_project]
                        if not df.empty: save_dataframe(df[df['專案'] != global_project])
                        save_settings(settings_data); save_prices(price_data)
                        st.session_state.mem_project = settings_data["projects"][0]
                        st.session_state[del_proj_key] = False
                        st.rerun()
                with dn:
                    if st.button("否", key="cancel_del_proj"): st.session_state[del_proj_key] = False; st.rerun()

    st.divider()
    
    st.subheader("📋 選單項目管理")
    st.caption(f"正在設定：**{global_project}**")
    
    if global_project in settings_data["items"]:
        p_items = settings_data["items"][global_project]
        if global_project not in price_data: price_data[global_project] = {}
        
        # 1. 匯入範本
        with st.expander("1. 從其他專案匯入選單範本", expanded=False):
            other_projects = [p for p in settings_data["projects"] if p != global_project]
            if not other_projects: st.info("無其他專案可匯入。")
            else:
                c_src, c_btn = st.columns([3, 1])
                with c_src: source_proj = st.selectbox("選擇來源", other_projects)
                with c_btn:
                    st.write("")
                    if st.button("📥 匯入", type="primary"):
                        src_items = settings_data["items"].get(source_proj, {})
                        tgt_items = settings_data["items"].get(global_project, {})
                        for cat, items in src_items.items():
                            if cat not in tgt_items: tgt_items[cat] = []
                            for item in items:
                                if item not in tgt_items[cat]: tgt_items[cat].append(item)
                        src_prices = price_data.get(source_proj, {})
                        if global_project not in price_data: price_data[global_project] = {}
                        for cat, p_items in src_prices.items():
                            if cat not in price_data[global_project]: price_data[global_project][cat] = {}
                            for iname, pval in p_items.items():
                                if iname not in price_data[global_project][cat]: price_data[global_project][cat][iname] = pval
                        save_settings(settings_data); save_prices(price_data)
                        st.success("匯入成功"); time.sleep(1); st.rerun()

        # 2. 新增管理項目
        with st.expander("2. 新增管理項目 (新增大標題)", expanded=False):
            c_n, c_t, c_b = st.columns([2, 2, 1])
            with c_n: new_block_name = st.text_input("區塊名稱 (如: 07.安全檢查)")
            with c_t: new_block_type = st.selectbox("類型", ["text", "usage", "cost"], format_func=lambda x: {"text": "文字", "usage": "數量", "cost": "成本"}[x])
            with c_b: 
                st.write("")
                if st.button("新增"):
                    new_key = new_block_name.split('.')[-1].strip() if '.' in new_block_name else new_block_name
                    if add_new_category_block(new_key, new_block_name, new_block_type, settings_data): st.success("已新增"); time.sleep(0.5); st.rerun()
                    else: st.error("區塊 Key 已存在")

        # 3. 既有選單項目管理
        with st.expander("3. 既有選單項目管理 (修改大標題 / 細項內容)", expanded=True):
            st.markdown("##### 修改大標題名稱")
            for i, config in enumerate(CAT_CONFIG_LIST):
                c_old, c_new, c_act, c_del = st.columns([2, 2, 1, 1])
                with c_old: st.text(f"原: {config['display']}")
                with c_new: new_disp = st.text_input(f"新名稱 {i}", value=config['display'], label_visibility="collapsed")
                with c_act: 
                    if new_disp != config['display']:
                        if st.button("更新", key=f"upd_cat_{i}"):
                            update_category_config(i, new_disp, settings_data); st.success("更新成功"); time.sleep(0.5); st.rerun()
                with c_del:
                    del_key = f"del_verify_{i}"
                    if del_key not in st.session_state: st.session_state[del_key] = False
                    if not st.session_state[del_key]:
                        if st.button("🗑️", key=f"btn_del_cat_{i}"): st.session_state[del_key] = True; st.rerun()
                    else:
                        if st.button("是", key=f"yes_del_{i}"): delete_category_block(i, settings_data); del st.session_state[del_key]; st.rerun()
                        if st.button("否", key=f"no_del_{i}"): st.session_state[del_key] = False; st.rerun()
            
            # --- 按照指示將管理功能放在紅線下方 ---
            st.markdown("---")
            st.markdown("##### 管理項目細項內容")

            cat_options = [c["display"] for c in CAT_CONFIG_LIST]
            target_display = st.selectbox("選擇要管理項目的類別", cat_options)
            target_config = next((c for c in CAT_CONFIG_LIST if c["display"] == target_display), None)
            
            if target_config:
                target_key = target_config["key"]
                cat_type = target_config["type"]
                curr_list = p_items.get(target_key, [])
                
                c_add, c_act = st.columns([3, 1])
                with c_add: new_option = st.text_input(f"在【{target_display}】新增選單項目", key=f"new_opt_{target_key}")
                with c_act:
                    st.write(""); st.write("")
                    if st.button("➕ 加入項目", key=f"btn_add_{target_key}"):
                        if new_option and new_option not in curr_list:
                            settings_data["items"][global_project][target_key].append(new_option)
                            save_settings(settings_data); st.success(f"已加入"); time.sleep(0.5); st.rerun()

                st.markdown(f"**目前項目列表 ({len(curr_list)})**")
                
                if cat_type == 'cost':
                    h1, h2, h3, h4, h5, h6 = st.columns([2, 2, 1, 1, 1, 1])
                    h1.caption("原名稱"); h2.caption("改名"); h3.caption("單價"); h4.caption("單位"); h5.caption("存"); h6.caption("刪")
                else:
                    h1, h2, h5, h6 = st.columns([3, 3, 1, 1])
                    h1.caption("原名稱"); h2.caption("改名"); h5.caption("存"); h6.caption("刪")

                for item in curr_list:
                    if cat_type == 'cost': c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 1, 1, 1, 1])
                    else: c1, c2, c5, c6 = st.columns([3, 3, 1, 1])
                    
                    with c1: st.text(item)
                    with c2: new_name = st.text_input("RN", value=item, key=f"rn_{target_key}_{item}", label_visibility="collapsed")
                    
                    new_p, new_u = 0, ""
                    if cat_type == 'cost':
                        if target_key not in price_data[global_project]: price_data[global_project][target_key] = {}
                        p_info = price_data[global_project][target_key].get(item, {"price": 0, "unit": "工"})
                        with c3: new_p = st.number_input("P", value=float(p_info["price"]), key=f"p_{target_key}_{item}", label_visibility="collapsed")
                        with c4: new_u = st.text_input("U", value=p_info["unit"], key=f"u_{target_key}_{item}", label_visibility="collapsed")
                    
                    with c5:
                        if st.button("💾", key=f"sv_{target_key}_{item}"):
                            if new_name != item: update_item_name(global_project, target_key, item, new_name, settings_data, price_data)
                            if cat_type == 'cost':
                                fin_name = new_name if new_name != item else item
                                price_data[global_project][target_key][fin_name] = {"price": new_p, "unit": new_u}
                                save_prices(price_data)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()
                    with c6:
                        if st.button("🗑️", key=f"dl_{target_key}_{item}"):
                            settings_data["items"][global_project][target_key].remove(item)
                            save_settings(settings_data); st.rerun()