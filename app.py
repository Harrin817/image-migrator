import streamlit as st
import re
import io
import os
import json
import time
import random
import zipfile
from openai import OpenAI
from curl_cffi import requests as curl_requests
from curl_cffi import CurlMime

# ==========================================
# 页面基础配置
# ==========================================
st.set_page_config(page_title="薄荷猫の图床全自动迁移", page_icon="🐱", layout="wide")

st.title("薄荷猫の图床全自动迁移工具")
import streamlit as st

st.markdown("""
**最新升级**：✅ 自动记忆 API 配置 ✅ 智能提取纯净直链 ✅ 支持 Word 文档 ✅ 动态压缩包命名<br>
*(全程在本地运行，安全、极速、不破坏原代码与排版结构)*<br>
**薄荷猫出品，🈲二传二改**
""", unsafe_allow_html=True)

# ==========================================
# 配置持久化逻辑 (本地保存为 config.json)
# ==========================================
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(endpoint, key):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"endpoint": endpoint, "key": key}, f)

# 读取上次保存的配置
user_config = load_config()
default_endpoint = user_config.get("endpoint", "https://api.openai.com/v1")
default_key = user_config.get("key", "")

# ==========================================
# 侧边栏：AI API 配置
# ==========================================
with st.sidebar:
    st.header("⚙️ 1. 大模型 API 配置")
    api_endpoint = st.text_input("API Endpoint", value=default_endpoint)
    api_key = st.text_input("API Key", type="password", value=default_key, placeholder="sk-...")

    if "available_models" not in st.session_state:
        st.session_state.available_models = []

    if st.button("🔄 测试连接并拉取模型 (自动保存配置)", use_container_width=True):
        if not api_key:
            st.error("请先填写 API Key！")
        else:
            # 用户点击时，自动将配置保存到本地文件
            save_config(api_endpoint, api_key)

            with st.spinner("正在连接 API 获取模型..."):
                try:
                    client = OpenAI(api_key=api_key, base_url=api_endpoint)
                    models_page = client.models.list()
                    models = [m.id for m in models_page.data]
                    models.sort()
                    st.session_state.available_models = models
                    st.success(f"成功拉取 {len(models)} 个可用模型！配置已永久保存。")
                except Exception as e:
                    st.error(f"连接失败: {e}")

    selected_model = st.selectbox(
        "选择要使用的模型",
        options=st.session_state.available_models if st.session_state.available_models else ["请先拉取模型"],
        disabled=not bool(st.session_state.available_models)
    )

# ==========================================
# 核心转存逻辑 (带直链提取)
# ==========================================
def upload_via_curl_cffi(img_url, postimages_session):
    img_resp = postimages_session.get(img_url, timeout=15)
    img_resp.raise_for_status()
    img_bytes = img_resp.content
    if len(img_bytes) < 1000:
        raise Exception("下载失败：文件太小，可能被拦截。")

    ext = img_url.split(".")[-1].split("?")[0][:4].lower()
    if ext not in ["png", "jpg", "jpeg", "gif", "webp"]:
        ext = "png"

    timestamp = str(int(time.time() * 1000))
    random_str = str(random.random())[1:]
    upload_session = timestamp + random_str

    mp = CurlMime()
    mp.addpart(name="upload_session", data=upload_session.encode('utf-8'))
    mp.addpart(name="optsize", data=b"0")
    mp.addpart(name="expire", data=b"0")
    mp.addpart(name="numfiles", data=b"1")
    mp.addpart(name="gallery", data=b"")
    mp.addpart(name="file", content_type=f"image/{ext}", filename=f"image.{ext}", data=img_bytes)

    res = postimages_session.post("https://postimages.org/json", multipart=mp, timeout=20)
    if res.status_code == 403:
        raise Exception("403 Forbidden. 遭到服务器盾拦截。")

    res.raise_for_status()
    data_json = res.json()
    if "url" not in data_json:
        raise Exception(f"转存被拒绝: {data_json.get('error', str(data_json))}")

    gallery_url = data_json["url"]

    # 直链提取逻辑
    try:
        page_resp = postimages_session.get(gallery_url, timeout=15)
        direct_match = re.search(r'id="code_direct"\s+value="([^"]+)"', page_resp.text)
        if direct_match:
            return direct_match.group(1)
        meta_match = re.search(r'property="og:image"\s+content="([^"]+)"', page_resp.text)
        if meta_match:
            return meta_match.group(1)
    except Exception:
        pass

    return gallery_url

# ==========================================
# 辅助函数：解剖 Word 文档提取和替换
# ==========================================
URL_PATTERN = r'https?://[^\s"\'()<>{}]+?\.(?:png|jpg|jpeg|gif|webp)'

def extract_urls_from_docx(file_bytes):
    urls = set()
    with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as z:
        for filename in z.namelist():
            if filename.endswith('.xml') or filename.endswith('.rels'):
                content = z.read(filename).decode('utf-8', errors='ignore')
                urls.update(re.findall(URL_PATTERN, content, flags=re.IGNORECASE))
    return list(urls)

def replace_urls_in_docx(file_bytes, url_mapping):
    new_docx = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as z_in, zipfile.ZipFile(new_docx, 'w', zipfile.ZIP_DEFLATED) as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if item.filename.endswith('.xml') or item.filename.endswith('.rels'):
                content = data.decode('utf-8', errors='ignore')
                for old_url, new_url in url_mapping.items():
                    content = content.replace(old_url, new_url)
                z_out.writestr(item, content.encode('utf-8'))
            else:
                z_out.writestr(item, data)
    return new_docx.getvalue()

# ==========================================
# 主操作区：文件上传与流水线
# ==========================================
st.header("📁 2. 上传处理区")
uploaded_files = st.file_uploader("拖拽美化文件 (支持 .txt, .css, .md, .docx 等)", accept_multiple_files=True)

if st.button("🚀 启动全自动流水线", type="primary", use_container_width=True):
    if not api_key or selected_model == "请先拉取模型":
        st.error("❌ 缺少大模型配置！请先在侧边栏测试连接并拉取模型。")
        st.stop()
    if not uploaded_files:
        st.warning("⚠️ 请至少上传一个文件。")
        st.stop()

    ai_client = OpenAI(api_key=api_key, base_url=api_endpoint)
    pi_session = curl_requests.Session(impersonate="chrome120")
    pi_session.headers.update({
        "Referer": "https://postimages.org/",
        "Origin": "https://postimages.org",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest"
    })

    try:
        pi_session.get("https://postimages.org/", timeout=10).raise_for_status()
    except Exception as e:
        st.error(f"❌ 无法连接到 Postimages 图床。({e})")
        st.stop()

    processed_files = []
    global_url_mapping = {}

    for uploaded_file in uploaded_files:
        with st.expander(f"⚙️ 正在处理: {uploaded_file.name}", expanded=True):
            file_bytes = uploaded_file.read()
            is_docx = uploaded_file.name.lower().endswith(".docx")
            content = ""
            found_urls = []

            # 阶段一：提取 URL
            st.markdown("##### 🧠 阶段 1/3：正在深度提取链接...")
            if is_docx:
                found_urls = extract_urls_from_docx(file_bytes)
                st.success(f"从 Word 底层 XML 中精准提取到 {len(found_urls)} 个图片链接。")
            else:
                try:
                    content = file_bytes.decode("utf-8")
                    prompt = f"提取以下文本中所有的图片 URL，只输出URL本身，一行一个，不要其他解释。如果没有输出 NONE。\n\n{content}"
                    response = ai_client.chat.completions.create(
                        model=selected_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1
                    )
                    ai_output = response.choices[0].message.content.strip()
                    if ai_output != "NONE" and ai_output:
                        found_urls = list(set([line.strip() for line in ai_output.split("\n") if line.strip().startswith("http")]))
                    st.success(f"AI 提取完毕！共找到 {len(found_urls)} 个链接。")
                except Exception as e:
                    st.error(f"解析纯文本失败: {e}")
                    continue

            if not found_urls:
                st.info("未找到链接，已跳过。")
                processed_files.append((uploaded_file.name, file_bytes))
                continue

            # 阶段二：本地转存 & 提取直链
            st.markdown("##### 🌐 阶段 2/3：正在突破防线提取直链...")
            progress_bar = st.progress(0)
            log_container = st.empty()

            file_url_mapping = {}
            for i, old_url in enumerate(found_urls):
                if old_url in global_url_mapping:
                    file_url_mapping[old_url] = global_url_mapping[old_url]
                else:
                    try:
                        new_url = upload_via_curl_cffi(old_url, pi_session)
                        file_url_mapping[old_url] = new_url
                        global_url_mapping[old_url] = new_url
                        log_container.success(f"✓ {old_url[:30]}... -> 获取成功")
                    except Exception as e:
                        log_container.error(f"✗ {old_url[:30]}... -> 失败: {e}")
                    time.sleep(1.5)
                progress_bar.progress((i + 1) / len(found_urls))

            # 阶段三：无损替换
            st.markdown("##### ✂️ 阶段 3/3：执行内存级无损替换...")
            if is_docx:
                new_file_bytes = replace_urls_in_docx(file_bytes, file_url_mapping)
                processed_files.append((uploaded_file.name, new_file_bytes))
            else:
                new_content = content
                for old_url, new_url in file_url_mapping.items():
                    new_content = new_content.replace(old_url, new_url)
                processed_files.append((uploaded_file.name, new_content.encode("utf-8")))

            st.success("🎉 文件重组完毕！排版结构完美保留。")

    # ==========================================
    # 下载打包区 (动态命名 ZIP)
    # ==========================================
    if processed_files:
        st.markdown("---")
        st.header("🎁 3. 提取成果")

        # 1. 组装直链映射 TXT 文件
        mapping_txt_content = "========== 直链映射对照表 ==========\n"
        for old_url, new_url in global_url_mapping.items():
            mapping_txt_content += f"{old_url} -> {new_url}\n"
        processed_files.append(("url_mapping_直链对照表.txt", mapping_txt_content.encode("utf-8")))

        # 2. 获取第一个上传文件的基础名称，用于动态命名 ZIP 包
        first_file_name = os.path.splitext(uploaded_files[0].name)[0]
        if len(uploaded_files) == 1:
            zip_filename = f"{first_file_name}_替换完成包.zip"
        else:
            zip_filename = f"{first_file_name}_等_批量替换包.zip"

        # 3. 压入 ZIP 内存缓冲区
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for fname, fbytes in processed_files:
                zip_file.writestr(fname, fbytes)

        st.download_button(
            label=f"⬇️ 一键打包下载 ({zip_filename})",
            data=zip_buffer.getvalue(),
            file_name=zip_filename,
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )
