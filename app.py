import streamlit as st
import pandas as pd
import os
import json
import time
import shutil
import copy
import altair as alt
import streamlit.components.v1 as components
import zipfile
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 設定頁面
st.set_page_config(page_title="多專案施工管理系統 (雲端旗艦版)", layout="wide", page_icon="☁️")

# --- 檔案路徑 ---
DATA_FILE = 'construction_data.csv' 
SETTINGS_FILE = 'settings.json'
TYPES_FILE = 'category_types.json'
PRICES_FILE = 'item_prices.json'
KEY_FILE = 'service_key.json' # 本機金鑰檔名
SHEET_NAME = 'construction_db' # 雲端試算表名稱

# --- 台灣例假日資料庫 (2025-2026) ---
HOLIDAYS = {
    "2025-01-01": "元旦", "2025-01-27": "小年夜", "2025-01-28": "除夕", "2025-01-29": "春節", "2025-01-30": "初二", "2025-01-31": "初三",
    "2025-02-28": "和平紀念日", "2025-04-04": "兒童節/清明節", "2025-05-01": "勞動節", "2025-05-31": "端午節",
    "2025-10-06": "中秋節", "2025-10-10": "國慶日",
    "2026-01-01": "元旦", "2026-02-16": "小年夜", "2026-02-17": "除夕", "2026-02-18": "春節",
    "2026-02-28": "和平紀念日", "2026-04-04": "兒童節", "2026-04-05": "清明節", "2026-05-01": "勞動節",
    "2026-06-19": "端午節", "2026-09-25": "中秋節", "2026-10-10": "國慶日"
}

# --- 預設範本 ---
DEFAULT_TEMPLATE = {
    "施工說明": ["正常施工", "暫停施工", "收尾階段", "驗收缺失改善", "天候不佳"],
    "相關紀錄": ["本日會議", "主管走動", "重要事件紀錄", "工安事項", "會勘紀錄"],
    "進料管理": ["鋼筋進場", "水泥進場", "磁磚進場", "設備進場", "其他材料"],
    "用料管理": ["混凝土 3000psi", "混凝土 2500psi", "CLSM", "級配", "水泥砂漿"],
    "工種 (人力)": ["粗工", "泥作", "水電", "油漆", "木工", "鐵工", "板模", "綁鐵", "打石", "清潔"],
    "機具 (設備)": ["挖土機 (怪手)", "山貓", "吊車", "發電機", "空壓機", "破碎機", "夯實機", "貨車"]
}

ORDER_MAP = {
    "施工說明": "01. 施工說明", "相關紀錄": "02. 相關紀錄", "進料管理": "03. 進料管理",
    "用料管理": "04. 用料管理", "工種 (人力)": "05. 工種 (人力)", "機具 (設備)": "06. 機具 (設備)"
}

DEFAULT_TYPES = {
    "施工說明": "text", "相關紀錄": "text", "進料管理": "text",
    "用料管理": "usage", "工種 (人力)": "cost", "機具 (設備)": "cost"
}

COST_CATEGORIES = [k for k, v in DEFAULT_TYPES.items() if v == 'cost']

# ==========================================
# 1. 核心邏輯 (雲端/本機 雙模式 - 強化日期容錯)
# ==========================================

@st.cache_resource
def get_google_sheet():
    """建立並快取 Google Sheet 連線 (支援雲端 Secrets 與 本機檔案)"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = None
    
    # 1. 優先檢查本機有沒有 service_key.json (電腦版模式)
    if os.path.exists(KEY_FILE):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        except Exception as e:
            st.error(f"本機金鑰讀取錯誤: {e}")
            return None
            
    # 2. 如果本機沒有檔案，再嘗試讀取 Streamlit Secrets (雲端/手機版模式)
    else:
        try:
            if "gcp_service_account" in st.secrets:
                creds_dict = st.secrets["gcp_service_account"]
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        except Exception:
            return None

    if creds is None:
        return None

    try:
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        return sheet
    except gspread.SpreadsheetNotFound:
        return "NOT_FOUND"
    except Exception as e:
        st.error(f"連線錯誤: {e}")
        return None

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
    data = load_json(SETTINGS_FILE, {"projects": ["預設專案"], "items": {"預設專案": copy.deepcopy(DEFAULT_TEMPLATE)}})
    if "items" in data:
        for proj, items in data["items"].items():
            if "施工說明" not in items:
                old = next((k for k in items.keys() if "施工概況" in k or "施工說明" in k), None)
                items["施工說明"] = items.pop(old) if old else DEFAULT_TEMPLATE["施工說明"]
            if "相關紀錄" not in items:
                old = next((k for k in items.keys() if "文字紀錄" in k or "相關紀錄" in k), None)
                items["相關紀錄"] = items.pop(old) if old else DEFAULT_TEMPLATE["相關紀錄"]
        save_json(SETTINGS_FILE, data)
    return data

def load_prices(): 
    data = load_json(PRICES_FILE, {})
    modified = False
    for proj in data:
        for cat in data[proj]:
            for item, val in data[proj][cat].items():
                if not isinstance(val, dict):
                    default_unit = "工" if "工種" in cat or "人力" in cat else "式"
                    data[proj][cat][item] = {"price": val, "unit": default_unit}
                    modified = True
    if modified: save_json(PRICES_FILE, data)
    return data

def save_prices(data): save_json(PRICES_FILE, data)
def save_types(data): save_json(TYPES_FILE, data)

def load_data():
    """從 Google Sheet 讀取資料 (強化日期相容性)"""
    cols = ['日期', '專案', '類別', '名稱', '單位', '數量', '單價', '總價', '備註', '月份']
    sheet = get_google_sheet()
    
    if sheet == "NOT_FOUND":
        st.error(f"找不到雲端試算表：{SHEET_NAME}。")
        return pd.DataFrame(columns=cols)
    elif sheet is None:
        st.warning("⚠️ 未偵測到金鑰。請確認 service_key.json 存在(電腦版) 或 Secrets 已設定(手機版)。")
        return pd.DataFrame(columns=cols)

    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=cols)
        
        # 先轉 DataFrame
        df = pd.DataFrame(data)
        
        # 補齊缺失欄位
        for c in cols:
            if c not in df.columns: df[c] = ""
            
        # 確保文字欄位是字串
        for col in ['專案', '類別', '名稱', '單位', '備註']:
            df[col] = df[col].fillna("").astype(str)
            
        # 類別名稱修正 (相容舊資料)
        df['類別'] = df['類別'].replace({
            '本日施工概況': '施工說明', '01.本日施工概況': '施工說明',
            '現場文字紀錄': '相關紀錄', '相關紀錄(會議、會勘、走動管理等)': '相關紀錄'
        })
        
        # --- 關鍵修正：智慧日期解析 ---
        # 使用 errors='coerce' 讓看不懂的日期變成 NaT (空值)，而不是直接報錯 Crash
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        
        # 移除日期無效的行 (避免整頁壞掉)
        df = df.dropna(subset=['日期'])
        
        # 轉回純日期格式 (去除時間 00:00:00)
        df['日期'] = df['日期'].dt.date
        
        # 重新計算月份
        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        # ---------------------------

        for col in ['總價', '數量', '單價']: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"雲端讀取錯誤: {e}")
        return pd.DataFrame(columns=cols)

def save_dataframe(df):
    try:
        sheet = get_google_sheet()
        if not sheet or sheet == "NOT_FOUND": return
        cols_drop = [c for c in ['月份', '刪除', 'temp_month', '星期/節日'] if c in df.columns]
        df_save = df.drop(columns=cols_drop)
        df_save['日期'] = df_save['日期'].astype(str)
        sheet.clear()
        sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
    except Exception as e: st.error(f"雲端存檔錯誤: {e}")

def append_data(date, project, category, name, unit, qty, price, note):
    total = qty * price if category in COST_CATEGORIES else 0
    row = [str(date), project, category, name, unit, qty, price, total, note]
    try:
        sheet = get_google_sheet()
        if sheet and sheet != "NOT_FOUND": sheet.append_row(row)
        else: st.error("無法連線至雲端")
    except Exception as e: st.error(f"雲端寫入錯誤: {e}")

def update_by_scope(original_df, edited_part, proj, month, cats):
    original_df['temp_month'] = pd.to_datetime(original_df['日期']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['專案'] == proj) & (original_df['類別'].isin(cats))
    df_kept = original_df[~mask].copy()
    edited_clean = edited_part.drop(columns=[c for c in ['刪除', '星期/節日'] if c in edited_part.columns])
    for col in ['數量', '單價']: edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    edited_clean['總價'] = edited_clean.apply(lambda r: r['數量']*r['單價'] if r['類別'] in COST_CATEGORIES else 0, axis=1)
    return pd.concat([df_kept, edited_clean], ignore_index=True)

def rename_item_in_project(project, category, old_item, new_item, settings, prices):
    curr = settings["items"][project][category]
    if new_item in curr: return False
    curr[curr.index(old_item)] = new_item
    if project in prices and category in prices[project] and old_item in prices[project][category]:
        prices[project][category][new_item] = prices[project][category].pop(old_item)
        save_prices(prices)
    df = load_data()
    if not df.empty:
        df.loc[(df['專案']==project) & (df['類別']==category) & (df['名稱']==old_item), '名稱'] = new_item
        save_dataframe(df)
    save_settings(settings); return True

def rename_project_logic(old_name, new_name, settings, prices):
    if new_name in settings["projects"]: return False, "名稱重複"
    idx = settings["projects"].index(old_name)
    settings["projects"][idx] = new_name
    settings["items"][new_name] = settings["items"].pop(old_name)
    if old_name in prices: prices[new_name] = prices.pop(old_name)
    save_prices(prices); save_settings(settings)
    df = load_data()
    if not df.empty:
        df.loc[df['專案'] == old_name, '專案'] = new_name
        save_dataframe(df)
    return True, "成功"

def create_zip_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        df = load_data()
        csv_buffer = io.StringIO()
        df.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日'] if c in df.columns]).to_csv(csv_buffer, index=False)
        zip_file.writestr(DATA_FILE, csv_buffer.getvalue())
        files_to_backup = [SETTINGS_FILE, PRICES_FILE, TYPES_FILE]
        for file in files_to_backup:
            if os.path.exists(file): zip_file.write(file)
    buffer.seek(0); return buffer

# --- 初始化 ---
settings_data = load_settings()
category_types = load_json(TYPES_FILE, DEFAULT_TYPES)
price_data = load_prices()
all_cats = set()
for p in settings_data["items"]:
    for c in settings_data["items"][p]: all_cats.add(c)
for c in all_cats: 
    if c not in category_types: category_types[c] = "text"
save_json(TYPES_FILE, category_types)

df = load_data()

if 'mem_project' not in st.session_state: st.session_state.mem_project = settings_data["projects"][0] if settings_data["projects"] else "預設專案"
if 'mem_date' not in st.session_state: st.session_state.mem_date = datetime.now()
if 'last_check_date' not in st.session_state: st.session_state.last_check_date = st.session_state.mem_date

# ==========================================
# 主介面
# ==========================================
st.title("🏗️ 多專案施工管理系統 (雲端旗艦版)")

sheet_status = get_google_sheet()
if sheet_status is None:
    st.warning("⚠️ 系統未偵測到金鑰。如果是電腦版請檢查 `service_key.json`；如果是手機版請檢查 `Secrets` 設定。")
else:
    with st.sidebar:
        st.header("📅 日期與專案設定")
        proj_list = settings_data["projects"]
        if st.session_state.mem_project not in proj_list:
            st.session_state.mem_project = proj_list[0]
        idx_proj = proj_list.index(st.session_state.mem_project)
        global_project = st.selectbox("🏗️ 目前專案", proj_list, index=idx_proj, key="global_proj")
        global_date = st.date_input("📅 工作日期", st.session_state.mem_date, key="global_date")
        
        if global_date != st.session_state.last_check_date:
            st.session_state.last_check_date = global_date
            components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)

        day_str, is_red_day = get_date_info(global_date)
        if is_red_day: st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
        else: st.markdown(f"### {global_date} {day_str}")

        st.session_state.mem_project = global_project
        st.session_state.mem_date = global_date

        if global_project not in settings_data["items"]:
            settings_data["items"][global_project] = copy.deepcopy(DEFAULT_TEMPLATE); save_settings(settings_data)
        current_items = settings_data["items"][global_project]

    tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "⚙️ 設定與管理"])

    with tab_entry:
        st.info(f"正在填寫：**{global_project}** / **{global_date} {day_str}**")
        d_key = str(global_date)

        with st.expander("📝 01. 施工說明及相關紀錄", expanded=True):
            cols_g1 = st.columns(2)
            with cols_g1[0]: 
                real_cat = next((c for c in current_items if "施工" in c), None)
                if real_cat:
                    st.markdown("**1. 施工說明**")
                    with st.form(key=f"form_status_{d_key}"):
                        txt_item = st.selectbox("項目", current_items[real_cat], key=f"sel_status_{d_key}")
                        txt_content = st.text_area("內容", height=100, key=f"area_status_{d_key}")
                        if st.form_submit_button("💾 儲存"):
                            append_data(global_date, global_project, real_cat, txt_item, "式", 1, 0, txt_content)
                            st.toast("已儲存"); time.sleep(0.5)
            with cols_g1[1]:
                real_cat = next((c for c in current_items if "紀錄" in c or "記錄" in c), None)
                if real_cat:
                    st.markdown("**2. 相關紀錄**")
                    with st.form(key=f"form_records_{d_key}"):
                        txt_item = st.selectbox("項目", current_items[real_cat], key=f"sel_records_{d_key}")
                        txt_content = st.text_area("內容", height=100, key=f"area_records_{d_key}")
                        if st.form_submit_button("💾 儲存"):
                            append_data(global_date, global_project, real_cat, txt_item, "式", 1, 0, txt_content)
                            st.toast("已儲存"); time.sleep(0.5)

        with st.expander("🚛 02. 進料管理紀錄", expanded=True):
            real_cat = next((c for c in current_items if "進料" in c), None)
            if real_cat:
                cols_g2 = st.columns(3)
                for i in range(3):
                    with cols_g2[i]:
                        st.markdown(f"**進料 {i+1}**")
                        with st.form(key=f"form_in_{i}_{d_key}"):
                            in_item = st.selectbox("材料名稱", current_items[real_cat], key=f"in_sel_{i}_{d_key}")
                            c_q, c_u = st.columns(2)
                            with c_q: in_qty = st.number_input("數量", min_value=0.0, step=1.0, key=f"in_q_{i}_{d_key}")
                            with c_u: in_unit = st.text_input("單位", value="式", key=f"in_u_{i}_{d_key}")
                            in_note = st.text_input("備註", key=f"in_n_{i}_{d_key}")
                            if st.form_submit_button("💾 儲存"):
                                append_data(global_date, global_project, real_cat, in_item, in_unit, in_qty, 0, in_note)
                                st.toast("已儲存"); time.sleep(0.5)

        with st.expander("🧱 03. 用料管理紀錄", expanded=True):
            real_cat = next((c for c in current_items if "用料" in c), None)
            if real_cat:
                cols_g3 = st.columns(3)
                for i in range(3):
                    with cols_g3[i]:
                        st.markdown(f"**用料 {i+1}**")
                        with st.form(key=f"form_use_{i}_{d_key}"):
                            use_item = st.selectbox("材料名稱", current_items[real_cat], key=f"use_sel_{i}_{d_key}")
                            c_q, c_u = st.columns(2)
                            with c_q: use_qty = st.number_input("數量", min_value=0.0, step=0.5, key=f"use_q_{i}_{d_key}")
                            with c_u: use_unit = st.text_input("單位", value="m3", key=f"use_u_{i}_{d_key}")
                            use_note = st.text_input("備註", key=f"use_n_{i}_{d_key}")
                            if st.form_submit_button("💾 儲存"):
                                append_data(global_date, global_project, real_cat, use_item, use_unit, use_qty, 0, use_note)
                                st.toast("已儲存"); time.sleep(0.5)

        with st.expander("👷 04. 人力與機具出工紀錄", expanded=True):
            cols_g4 = st.columns(2)
            with cols_g4[0]:
                cat = next((c for c in current_items if "工種" in c), None)
                if cat:
                    st.markdown("### 01. 工種 (人力)")
                    proj_prices = price_data.get(global_project, {}).get(cat, {})
                    cost_item = st.selectbox("項目", current_items[cat], key=f"sel_{cat}_{d_key}")
                    item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "工"})
                    unique_key = f"{cat}_{d_key}_{cost_item}"
                    c_q, c_p = st.columns(2)
                    with c_q: cost_qty = st.number_input("數量", min_value=0.0, step=0.5, value=1.0, key=f"qty_{unique_key}")
                    with c_p: cost_price = st.number_input("單價 ($)", value=item_setting["price"], step=100, key=f"price_{unique_key}")
                    cost_unit = st.text_input("單位", value=item_setting["unit"], key=f"unit_{unique_key}")
                    cost_note = st.text_input("備註", key=f"note_{unique_key}")
                    if st.button(f"💾 新增工種", type="primary", key=f"btn_{unique_key}"):
                        append_data(global_date, global_project, cat, cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("已儲存"); time.sleep(0.5); st.rerun()

            with cols_g4[1]:
                cat = next((c for c in current_items if "機具" in c), None)
                if cat:
                    st.markdown("### 02. 機具 (設備)")
                    proj_prices = price_data.get(global_project, {}).get(cat, {})
                    cost_item = st.selectbox("項目", current_items[cat], key=f"sel_{cat}_{d_key}")
                    item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "式"})
                    unique_key = f"{cat}_{d_key}_{cost_item}"
                    c_q, c_p = st.columns(2)
                    with c_q: cost_qty = st.number_input("數量", min_value=0.0, step=0.5, value=1.0, key=f"qty_{unique_key}")
                    with c_p: cost_price = st.number_input("單價 ($)", value=item_setting["price"], step=100, key=f"price_{unique_key}")
                    cost_unit = st.text_input("單位", value=item_setting["unit"], key=f"unit_{unique_key}")
                    cost_note = st.text_input("備註", key=f"note_{unique_key}")
                    if st.button(f"💾 新增機具", type="primary", key=f"btn_{unique_key}"):
                        append_data(global_date, global_project, cat, cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("已儲存"); time.sleep(0.5); st.rerun()

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
            
            def render_section(display_title, cats, key, cost=False, qty=False):
                sk = f"conf_{key}"; 
                if sk not in st.session_state: st.session_state[sk] = False
                sec_df = month_df[month_df['類別'].isin(cats)].copy()
                if not sec_df.empty:
                    st.subheader(display_title)
                    view = sec_df.copy()
                    if ed_date != "整個月": view = view[view['日期'] == ed_date]
                    if search: mask = view.apply(lambda x: search in str(x['名稱']) or search in str(x['備註']), axis=1); view = view[mask]
                    if not view.empty:
                        view['🗓️ 星期/節日'] = view['日期'].apply(lambda x: get_date_info(x)[0])
                        cols = list(view.columns)
                        cols.insert(1, cols.pop(cols.index('🗓️ 星期/節日')))
                        view = view[cols]
                    hidden = sec_df[~sec_df.index.isin(view.index)]
                    if '刪除' not in view.columns: view.insert(0, "刪除", False)
                    col_cfg = {"刪除": st.column_config.CheckboxColumn(width="small"), "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"), "🗓️ 星期/節日": st.column_config.TextColumn(disabled=True, width="medium"), "名稱": st.column_config.TextColumn(width="medium"), "備註": st.column_config.TextColumn(width="large"), "月份": None, "類別": None, "專案": None}
                    if cost: col_cfg.update({"單價": st.column_config.NumberColumn(width="small"), "總價": st.column_config.NumberColumn(disabled=True, width="small")})
                    else: col_cfg.update({"單價": None, "總價": None})
                    if qty: col_cfg.update({"數量": st.column_config.NumberColumn(width="small"), "單位": st.column_config.TextColumn(width="small")})
                    else: col_cfg.update({"數量": None, "單位": None})
                    edited = st.data_editor(view.sort_values('日期', ascending=False), key=f"e_{key}", column_config=col_cfg, use_container_width=True, hide_index=True)
                    b1, b2, _ = st.columns([1, 1, 6])
                    with b1: 
                        if st.button("💾 更新修改", key=f"s_{key}"): 
                            vis = edited.drop(columns=['刪除']); merged = pd.concat([hidden, vis], ignore_index=True); final = update_by_scope(df, merged, global_project, ed_month, cats); save_dataframe(final); st.success("已更新"); time.sleep(0.5); st.rerun()
                    with b2: 
                        if st.button("🗑️ 刪除選取", key=f"d_{key}", type="primary"): 
                            if not edited[edited['刪除']].empty: st.session_state[sk] = True
                    if st.session_state[sk]: 
                        st.warning("⚠️ 確定要刪除選取的資料嗎？此動作無法復原。")
                        cy, cn = st.columns([1, 5])
                        with cy:
                            if st.button("✔️ 是", key=f"y_{key}", type="primary"): 
                                vis = edited[~edited['刪除']].drop(columns=['刪除']); merged = pd.concat([hidden, vis], ignore_index=True); final = update_by_scope(df, merged, global_project, ed_month, cats); save_dataframe(final); st.session_state[sk] = False; st.rerun()
                        with cn:
                            if st.button("❌ 否 (取消)", key=f"n_{key}"): st.session_state[sk] = False; st.rerun()
            
            for base_key, display_name in ORDER_MAP.items():
                target_cats = [c for c in current_items if base_key in c]
                if target_cats:
                    is_cost = "工種" in base_key or "機具" in base_key
                    is_qty = "進料" in base_key or "用料" in base_key or is_cost
                    render_section(display_name, target_cats, f"sec_{base_key}", cost=is_cost, qty=is_qty)

    with tab_dash:
        if df.empty: st.info("無資料")
        else:
            dash_df = df[df['專案'] == global_project]
            if dash_df.empty: st.warning(f"專案【{global_project}】目前沒有資料。")
            else:
                today_str = datetime.now().date(); cur_month = today_str.strftime("%Y-%m")
                d_cost = dash_df[dash_df['日期'] == today_str]['總價'].sum()
                m_cost = dash_df[dash_df['月份'] == cur_month]['總價'].sum()
                t_cost = dash_df['總價'].sum()
                k1, k2, k3 = st.columns(3)
                k1.metric("今日費用", f"${d_cost:,.0f}"); k2.metric("本月費用", f"${m_cost:,.0f}"); k3.metric("專案總費用", f"${t_cost:,.0f}")
                st.divider()
                cost_df = dash_df[dash_df['總價'] > 0]
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
                        text = base.mark_text(radius=120).encode(text=alt.Text("總價", format=",.0f"), order=alt.Order("總價", sort="descending"), color=alt.value("black"))
                        st.altair_chart(pie + text, use_container_width=True)
                        st.divider()
                        col_man, col_mach = st.columns(2)
                        with col_man:
                            st.markdown("### 👷 人力費用明細")
                            man_data = chart_data[chart_data['類別'].str.contains("工種")]
                            if not man_data.empty:
                                man_bar = man_data.groupby('名稱')['總價'].sum().reset_index()
                                st.bar_chart(man_bar, x='名稱', y='總價', color="#FF6C6C")
                                st.dataframe(man_data[['日期', '名稱', '數量', '單價', '總價']], use_container_width=True, hide_index=True)
                                st.markdown(f"**人力總計: ${man_data['總價'].sum():,.0f}**")
                            else: st.info("無人力資料")
                        with col_mach:
                            st.markdown("### 🚜 機具費用明細")
                            mach_data = chart_data[chart_data['類別'].str.contains("機具")]
                            if not mach_data.empty:
                                mach_bar = mach_data.groupby('名稱')['總價'].sum().reset_index()
                                st.bar_chart(mach_bar, x='名稱', y='總價', color="#4B8BBE")
                                st.dataframe(mach_data[['日期', '名稱', '數量', '單價', '總價']], use_container_width=True, hide_index=True)
                                st.markdown(f"**機具總計: ${mach_data['總價'].sum():,.0f}**")
                            else: st.info("無機具資料")
                    else: st.info("此月份無費用資料")
                else: st.info("尚無金額紀錄。")

    with tab_settings:
        st.header("⚙️ 設定與管理")
        
        with st.expander("📦 資料備份中心", expanded=False):
            st.info("下載備份 (含雲端資料與本地設定)")
            st.download_button("📦 下載完整系統備份 (ZIP)", create_zip_backup(), file_name=f"full_backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
            st.divider()
            uploaded_file = st.file_uploader("📤 系統還原 (支援 ZIP 完整包或 CSV 純資料)", type=['csv', 'zip'])
            if uploaded_file and st.button("⚠️ 確認還原"):
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df_restore = pd.read_csv(uploaded_file)
                        save_dataframe(df_restore)
                        st.success("CSV 資料還原成功！"); time.sleep(1); st.rerun()
                    elif uploaded_file.name.endswith('.zip'):
                        with zipfile.ZipFile(uploaded_file, 'r') as z: 
                            z.extractall(".")
                            if DATA_FILE in z.namelist():
                                df_restore = pd.read_csv(DATA_FILE)
                                save_dataframe(df_restore)
                        st.success("完整系統還原成功！"); time.sleep(1); st.rerun()
                except Exception as e: st.error(f"還原失敗：{e}")

        with st.expander("1. 專案管理", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**新增專案**")
                with st.form("add_p"):
                    new_p = st.text_input("專案名稱")
                    if st.form_submit_button("➕ 新增"):
                        if new_p and new_p not in settings_data["projects"]: settings_data["projects"].append(new_p); settings_data["items"][new_p] = copy.deepcopy(DEFAULT_TEMPLATE); save_settings(settings_data); st.rerun()
            with c2:
                st.markdown("**修改專案名稱**")
                ren_p = st.text_input("修改為", value=global_project)
                if st.button("✏️ 確認改名"):
                    if ren_p != global_project:
                        suc, msg = rename_project_logic(global_project, ren_p, settings_data, price_data)
                        if suc: 
                            st.session_state.mem_project = ren_p
                            st.success(msg); time.sleep(1); st.rerun()
                        else: st.error(msg)
            with c3:
                st.markdown("**移除專案**")
                st.write("")
                confirm_del = st.checkbox("⚠️ 確定移除此專案", key="confirm_del_proj")
                if st.button("🗑️ 確認移除", type="primary", disabled=not confirm_del):
                    if len(settings_data["projects"]) > 1: settings_data["projects"].remove(global_project); del settings_data["items"][global_project]; save_settings(settings_data); st.session_state.mem_project = settings_data["projects"][0]; st.rerun()

        with st.expander("2. 匯入/複製 專案設定 (範本)", expanded=False):
            st.info("將其他專案的設定複製到目前專案。")
            src_opts = ["(系統預設範本)"] + [p for p in settings_data["projects"] if p != global_project]
            src_p = st.selectbox("選擇來源", src_opts)
            confirm_import = st.checkbox("⚠️ 確定覆蓋目前設定", key="confirm_import_proj")
            if st.button("📥 確認匯入", disabled=not confirm_import):
                src_items = DEFAULT_TEMPLATE if src_p == "(系統預設範本)" else settings_data["items"][src_p]
                settings_data["items"][global_project] = copy.deepcopy(src_items)
                save_settings(settings_data); st.success("匯入成功"); time.sleep(1); st.rerun()

        st.subheader("3. 獨立選單與預設單價/單位")
        st.caption(f"正在設定：**{global_project}**")
        
        if global_project in settings_data["items"]:
            p_items = settings_data["items"][global_project]
            if global_project not in price_data: price_data[global_project] = {}
            
            sorted_cats = []
            for base_key, display_name in ORDER_MAP.items():
                found = next((k for k in p_items.keys() if base_key in k), None)
                if found: sorted_cats.append((found, display_name))

            col_s1, col_s2, col_s3 = st.columns(3)
            for i, (cat, display_name) in enumerate(sorted_cats):
                col = [col_s1, col_s2, col_s3][i % 3]
                with col:
                    st.info(f"📁 {display_name}")
                    curr_list = p_items[cat]
                    if cat not in price_data[global_project]: price_data[global_project][cat] = {}
                    
                    with st.expander("編輯"):
                        with st.form(key=f"add_{cat}"):
                            ni = st.text_input("新增")
                            if st.form_submit_button("加入"): 
                                if ni not in curr_list: settings_data["items"][global_project][cat].append(ni); save_settings(settings_data); st.rerun()
                        
                        if cat in COST_CATEGORIES:
                            st.caption("💰 預設單價與單位")
                            for item_name in curr_list:
                                item_data = price_data[global_project][cat].get(item_name, {"price": 0, "unit": "工" if "工種" in cat else "式"})
                                c_p, c_u, c_b = st.columns([2, 1, 1])
                                with c_p: new_p = st.number_input(f"{item_name} 單價", value=item_data["price"], step=100, key=f"p_{cat}_{item_name}")
                                with c_u: new_u = st.text_input(f"單位", value=item_data["unit"], key=f"u_{cat}_{item_name}")
                                with c_b:
                                    st.write(""); st.write("")
                                    if st.button("✅ 設定", key=f"set_{cat}_{item_name}"):
                                        price_data[global_project][cat][item_name] = {"price": new_p, "unit": new_u}
                                        save_prices(price_data)
                                        st.toast(f"已儲存：{item_name}")
                            st.write("---")
                        
                        target = st.selectbox("選擇", curr_list, key=f"tgt_{cat}")
                        ren_txt = st.text_input("改名", value=target, key=f"ren_{cat}")
                        c_e, c_d = st.columns(2)
                        with c_e: 
                            if st.button("改名", key=f"btn_r_{cat}"): 
                                if ren_txt != target: rename_item_in_project(global_project, cat, target, ren_txt, settings_data, price_data); st.success("OK"); st.rerun()
                        with c_d:
                            if st.button("移除", key=f"btn_d_{cat}"): settings_data["items"][global_project][cat].remove(target); save_settings(settings_data); st.rerun()