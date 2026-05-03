import sys
import os
import json
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import groq
import streamlit.components.v1 as components

from config import MEAL_PLAN_PROMPT, TEXT_MODEL
from agents import meal_agent, shopping_agent
from services import apple_notes, vision, documents, persistence

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "chat_history.json")
DATABASE_URL = ""
try:
    DATABASE_URL = st.secrets.get("DATABASE_URL", "")  # type: ignore[attr-defined]
except Exception:
    DATABASE_URL = ""


# ── Chat history persistence ──────────────────────────────────────────────────

def get_query_uid() -> str:
    try:
        value = st.query_params.get("uid")
    except Exception:
        try:
            value = st.experimental_get_query_params().get("uid")
        except Exception:
            value = ""
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def set_query_uid(user_id: str | None):
    current = get_query_uid()
    target = user_id or ""
    if current == target:
        return
    try:
        if user_id:
            st.query_params["uid"] = user_id
        else:
            del st.query_params["uid"]
    except Exception:
        try:
            params = st.experimental_get_query_params()
            if user_id:
                params["uid"] = [user_id]
            else:
                params.pop("uid", None)
            st.experimental_set_query_params(
                **{k: (v[0] if isinstance(v, list) and v else v) for k, v in params.items()}
            )
        except Exception:
            pass


def get_or_create_user_id() -> str:
    if "user_id" in st.session_state and st.session_state.user_id:
        return st.session_state.user_id
    uid = get_query_uid()
    if not uid:
        uid = str(uuid.uuid4())
        set_query_uid(uid)
    st.session_state.user_id = uid
    return uid


def _db_engine():
    if not DATABASE_URL:
        return None
    try:
        return persistence.get_engine(DATABASE_URL)
    except Exception:
        return None


def load_history() -> dict:
    user_id = get_or_create_user_id()
    engine = _db_engine()
    if engine is not None:
        try:
            return persistence.load_sessions(engine, user_id)
        except Exception:
            return {}

    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                data = json.load(f)
                # Legacy file is not per-user; treat it as belonging to this uid.
                return data
        except Exception:
            pass
    return {}

def get_query_sid() -> str:
    try:
        value = st.query_params.get("sid")
    except Exception:
        try:
            value = st.experimental_get_query_params().get("sid")
        except Exception:
            value = ""
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def set_query_sid(session_id: str | None):
    current = get_query_sid()
    target = session_id or ""
    if current == target:
        return

    try:
        if session_id:
            st.query_params["sid"] = session_id
        else:
            del st.query_params["sid"]
    except Exception:
        try:
            params = st.experimental_get_query_params()
            if session_id:
                params["sid"] = [session_id]
            else:
                params.pop("sid", None)
            st.experimental_set_query_params(
                **{k: (v[0] if isinstance(v, list) and v else v) for k, v in params.items()}
            )
        except Exception:
            pass


def save_session(session_id: str, label: str, meal_msgs: list, shop_msgs: list):
    row = {
        "label": label,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meal_messages": meal_msgs,
        "shop_messages": shop_msgs,
        "location": st.session_state.get("location", ""),
        "active_notes": st.session_state.get("active_notes", ""),
        "active_note_names": st.session_state.get("active_note_names", []),
    }

    user_id = get_or_create_user_id()
    engine = _db_engine()
    if engine is not None:
        try:
            persistence.save_session(engine, user_id, session_id, row)
            return
        except Exception:
            pass

    history = load_history()
    history[session_id] = row
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def generate_title(client, first_user_message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Give this chat a short title (4-6 words max, no quotes, no punctuation).\n\n"
                    + first_user_message
                ),
            }],
        )
        return resp.choices[0].message.content.strip()[:50]
    except Exception:
        return first_user_message[:40]


def autosave(client):
    meal = st.session_state.get("meal_messages", [])
    shop = st.session_state.get("shop_messages", [])
    if not meal and not shop:
        return

    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    if "session_title" not in st.session_state:
        first_msg = (meal or shop)[0].get("content", "")
        if isinstance(first_msg, list):
            first_msg = next((p.get("text", "") for p in first_msg if p.get("type") == "text"), "Chat")
        st.session_state.session_title = generate_title(client, first_msg)

    save_session(
        st.session_state.current_session_id,
        st.session_state.session_title,
        meal,
        shop,
    )
    set_query_sid(st.session_state.current_session_id)


def delete_session(session_id: str):
    user_id = get_or_create_user_id()
    engine = _db_engine()
    if engine is not None:
        try:
            persistence.delete_session(engine, user_id, session_id)
            return
        except Exception:
            pass

    history = load_history()
    history.pop(session_id, None)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ── Groq client ───────────────────────────────────────────────────────────────

@st.cache_resource
def get_groq_client():
    return groq.Groq(api_key=st.secrets["GROQ_API_KEY"])


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Maple", page_icon="🗺️", layout="wide")

st.markdown(
    """
    <style>
      :root {
        --mm-accent: #c8a96a;
        --mm-border: rgba(255, 255, 255, 0.10);
        --mm-border-2: rgba(255, 255, 255, 0.08);
        --mm-muted: rgba(243, 244, 246, 0.70);
        --mm-panel: rgba(255, 255, 255, 0.04);
        --mm-panel-2: rgba(255, 255, 255, 0.06);
        --mm-radius: 18px;
        --mm-sidebar-width: 320px;
      }

      /* Reduce dead space, keep it chat-like */
      section.main > div.block-container {
        max-width: 1040px;
        padding-top: 0.85rem;
        padding-bottom: 1.85rem;
      }

      /* Top bar (title + tagline) */
      .mm-topbar {
        display: flex;
        align-items: baseline;
        gap: 14px;
        padding: 0.35rem 0 1rem 0;
        border-bottom: 1px solid var(--mm-border-2);
        margin-bottom: 0.7rem;
      }
      .mm-title {
        font-family: ui-serif, Georgia, Cambria, "Times New Roman", Times, serif;
        font-size: 2.35rem;
        letter-spacing: -0.02em;
        font-weight: 700;
        line-height: 1.05;
        color: rgba(255, 255, 255, 0.96);
      }
      .mm-tagline {
        font-size: 1.05rem;
        color: var(--mm-muted);
      }

      /* Tabs like the mock (underline + accent) */
      div[data-testid="stTabs"] {
        border-bottom: 1px solid var(--mm-border-2);
      }
      button[data-baseweb="tab"] {
        background: transparent !important;
        border-radius: 0 !important;
        padding: 0.65rem 0.6rem !important;
        color: rgba(243, 244, 246, 0.60) !important;
      }
      button[data-baseweb="tab"][aria-selected="true"] {
        color: var(--mm-accent) !important;
        border-bottom: 2px solid var(--mm-accent) !important;
      }

      /* Sidebar: width + section styling */
      section[data-testid="stSidebar"] {
        border-right: 1px solid var(--mm-border-2);
      }
      section[data-testid="stSidebar"]:not([aria-expanded="false"]),
      div[data-testid="stSidebar"]:not([aria-expanded="false"]) {
        width: var(--mm-sidebar-width) !important;
        max-width: var(--mm-sidebar-width) !important;
      }
      section[data-testid="stSidebar"] > div,
      div[data-testid="stSidebar"] > div {
        padding-top: 1.05rem;
        padding-left: 1.05rem;
        padding-right: 1.05rem;
      }
      .mm-side-label {
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.78rem;
        color: rgba(243, 244, 246, 0.46);
        margin: 0.4rem 0 0.55rem 0;
      }

      /* Sidebar buttons look like list items */
      section[data-testid="stSidebar"] .stButton button {
        width: 100%;
        text-align: left;
        justify-content: flex-start !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        border-radius: 14px !important;
        padding: 0.6rem 0.7rem !important;
        color: rgba(243, 244, 246, 0.78) !important;
      }
      section[data-testid="stSidebar"] .stButton button > div,
      section[data-testid="stSidebar"] .stButton button > div > div {
        justify-content: flex-start !important;
        text-align: left !important;
        width: 100%;
      }
      section[data-testid="stSidebar"] .stButton button:hover {
        background: rgba(255, 255, 255, 0.04) !important;
        border-color: rgba(255, 255, 255, 0.06) !important;
      }
      .mm-nav-active section[data-testid="stSidebar"] .stButton button {
        background: rgba(255, 255, 255, 0.06) !important;
        border-color: rgba(255, 255, 255, 0.10) !important;
      }

      /* Inputs */
      section[data-testid="stSidebar"] .stTextInput input {
        border-radius: 16px;
        padding-top: 0.75rem;
        padding-bottom: 0.75rem;
        font-size: 1.05rem;
      }
      section[data-testid="stSidebar"] .stTextInput div[data-baseweb="input"] {
        background: rgba(255, 255, 255, 0.04);
        border-radius: 16px;
      }

      /* Chat bubbles */
      div[data-testid="stChatMessage"] {
        margin: 0.85rem 0;
      }
      div[data-testid="stChatMessageContent"],
      div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
        background: var(--mm-panel);
        border: 1px solid var(--mm-border-2);
        border-radius: var(--mm-radius);
        padding: 0.8rem 0.95rem;
      }

      /* Empty state */
      .mm-empty {
        text-align: center;
        padding: 3.25rem 0 2.25rem 0;
      }
      .mm-avatar {
        width: 78px;
        height: 78px;
        border-radius: 999px;
        margin: 0 auto 18px auto;
        display: grid;
        place-items: center;
        background: rgba(200, 169, 106, 0.09);
        border: 1px solid rgba(200, 169, 106, 0.20);
        box-shadow: 0 0 0 10px rgba(200, 169, 106, 0.05);
        font-size: 30px;
      }
      .mm-hello {
        font-family: ui-serif, Georgia, Cambria, "Times New Roman", Times, serif;
        font-size: 2.45rem;
        line-height: 1.15;
        margin: 0 0 0.8rem 0;
      }
      .mm-hello em {
        color: var(--mm-accent);
        font-style: italic;
      }
      .mm-sub {
        color: rgba(243, 244, 246, 0.60);
        font-size: 1.1rem;
        line-height: 1.65;
        max-width: 720px;
        margin: 0 auto;
      }

      /* JS turns the real Streamlit popover button into the chat-box + button */
      div[data-testid="stPopover"] {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
      }
      [data-mealmap-attach] { display: none; }

      /* Chat composer (use native st.chat_input) */
      div[data-testid="stChatInput"] {
        position: relative !important;
      }
      div[data-testid="stChatInput"] textarea {
        border-radius: 999px !important;
        padding-left: 3.1rem !important; /* room for + */
        padding-right: 3.2rem !important; /* room for send */
        min-height: 56px !important;
        font-size: 1.05rem !important;
        line-height: 1.35 !important;
        background: rgba(255, 255, 255, 0.06) !important;
        border: 1px solid rgba(255, 255, 255, 0.10) !important;
      }

      div[data-testid="stPopover"][data-mealmap-active-attach="true"] {
        position: fixed !important;
        width: 38px !important;
        height: 38px !important;
        z-index: 2147483647 !important;
        pointer-events: auto !important;
      }

      div[data-testid="stPopover"][data-mealmap-active-attach="false"] {
        position: fixed !important;
        left: -10000px !important;
        top: -10000px !important;
        pointer-events: none !important;
      }

      button[data-mealmap-attach-button="true"] {
        width: 38px;
        height: 38px;
        min-width: 38px !important;
        min-height: 38px !important;
        padding: 0 !important;
        border-radius: 999px;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        background: rgba(255, 255, 255, 0.04) !important;
        color: transparent !important;
        box-shadow: none !important;
        display: grid;
        place-items: center;
        cursor: pointer !important;
      }

      button[data-mealmap-attach-button="true"] * {
        display: none !important;
      }

      button[data-mealmap-attach-button="true"]::before {
        content: "+";
        color: rgba(255, 255, 255, 0.92);
        font-size: 32px;
        font-weight: 300;
        line-height: 1;
        margin-top: -2px;
      }

      button[data-mealmap-attach-button="true"]:hover {
        background: rgba(255, 255, 255, 0.08) !important;
        border-color: rgba(255, 255, 255, 0.18) !important;
      }

      /* Make the native send button circular (best-effort) */
      div[data-testid="stChatInput"] button {
        border-radius: 999px !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="mm-topbar">
      <div class="mm-title">Maple</div>
      <div class="mm-tagline">plan meals • find best prices</div>
    </div>
    """,
    unsafe_allow_html=True,
)

def inject_client_helpers():
    components.html(
        """
        <script>
        (function () {
          let win;
          try {
            win = window.parent;
            void win.document;
          } catch (e1) {
            try {
              win = window.top;
              void win.document;
            } catch (e2) {
              return;
            }
	          }
	          const doc = win.document;
	          const state = win.__mealmap || (win.__mealmap = {});
	          try { if (state.interval) win.clearInterval(state.interval); } catch (e) {}
	          try { if (state.observer) state.observer.disconnect(); } catch (e) {}
	
	          const TAB_KEY = "mealmap.activeTab";

          function bindTabs() {
            const tabs = Array.from(doc.querySelectorAll('button[data-baseweb="tab"]'));
            if (!tabs.length) return false;

            tabs.forEach((btn, idx) => {
              if (btn.__mealmapBound) return;
              btn.__mealmapBound = true;
              btn.addEventListener("click", () => {
                try { win.localStorage.setItem(TAB_KEY, String(idx)); } catch (e) {}
              });
            });

            let saved = null;
            try { saved = win.localStorage.getItem(TAB_KEY); } catch (e) {}
            const i = saved != null ? parseInt(saved, 10) : null;
	            const active = tabs.findIndex((b) => b.getAttribute("aria-selected") === "true");
	            if (i != null && !Number.isNaN(i) && i >= 0 && i < tabs.length && i !== active) {
	              tabs[i].click();
	              try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); } catch (e) {}
	              setTimeout(() => { try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); } catch (e) {} }, 50);
	              setTimeout(() => { try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); } catch (e) {} }, 300);
	            }
	            return true;
	          }

          function getActivePanel() {
            const activeTab = doc.querySelector('button[data-baseweb="tab"][aria-selected="true"]');
            const panelId = activeTab && activeTab.getAttribute("aria-controls");
            return panelId ? doc.getElementById(panelId) : null;
          }

          function wireAttachButton() {
            const panel = getActivePanel() || doc;
            const chat = panel.querySelector('div[data-testid="stChatInput"]') || doc.querySelector('div[data-testid="stChatInput"]');
            if (!chat) return;

            const textarea =
              chat.querySelector('textarea') ||
              panel.querySelector('textarea[data-testid="stChatInputTextArea"]') ||
              doc.querySelector('textarea[data-testid="stChatInputTextArea"]');
            if (!textarea) return;

            const activePopover = panel.querySelector('div[data-testid="stPopover"]');
            if (!activePopover) return;

            const popoverButton = activePopover.querySelector('button');
            if (!popoverButton) return;

            for (const popover of doc.querySelectorAll('div[data-testid="stPopover"]')) {
              popover.setAttribute("data-mealmap-active-attach", popover === activePopover ? "true" : "false");
              const btn = popover.querySelector("button");
              if (btn) {
                btn.removeAttribute("data-mealmap-attach-button");
              }
            }

            popoverButton.setAttribute("data-mealmap-attach-button", "true");
            popoverButton.setAttribute("aria-label", "Attach a file or note");
            popoverButton.setAttribute("title", "Attach a file or note");

            // Position the real popover trigger on top of the left side of the chat input.
            try {
              const rect = textarea.getBoundingClientRect();
              activePopover.style.left = (rect.left + 12) + "px";
              activePopover.style.top = (rect.top + (rect.height / 2) - 19) + "px";
            } catch (e) {}
          }

          function getScrollContainers(panel) {
            const candidates = [
              doc.querySelector('div[data-testid="stAppViewContainer"]'),
              doc.querySelector('div[data-testid="stMainBlockContainer"]'),
              doc.querySelector('section.main'),
              doc.scrollingElement,
              doc.documentElement,
              doc.body,
            ];
            if (panel) candidates.unshift(panel);
            return candidates.filter(Boolean);
          }

          function scrollBottom() {
            const panel = getActivePanel() || doc;

            const msgs = panel.querySelectorAll('[data-testid="stChatMessage"]');
            const last = msgs.length ? msgs[msgs.length - 1] : null;
            if (last && last.scrollIntoView) {
              try { last.scrollIntoView({ behavior: "smooth", block: "end" }); } catch (e) {}
            }

            const chatInput =
              panel.querySelector('[data-testid="stChatInput"]') ||
              doc.querySelector('[data-testid="stChatInput"]') ||
              doc.querySelector('textarea[data-testid="stChatInputTextArea"]') ||
              panel.querySelector('div[data-testid="stTextArea"] textarea') ||
              doc.querySelector('div[data-testid="stTextArea"] textarea');
            if (chatInput && chatInput.scrollIntoView) {
              try { chatInput.scrollIntoView({ behavior: "smooth", block: "end" }); } catch (e) {}
            }

            for (const node of getScrollContainers(panel)) {
              try {
                if (node.scrollTo) node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
                else node.scrollTop = node.scrollHeight;
              } catch (e) {}
            }
	          }
	
	          win.__mealmapScrollBottom = scrollBottom;
	          win.__mealmapForceScroll = false;
	          state.scrollBottom = scrollBottom;
	          state.forceScroll = false;

          let stickToBottom = true;
          function updateStick() {
            const scroller =
              doc.querySelector('div[data-testid="stAppViewContainer"]') ||
              doc.querySelector('section.main') ||
              doc.scrollingElement;
            if (!scroller) return;
            const dist = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
            stickToBottom = dist < 120;
          }
          doc.addEventListener("scroll", updateStick, true);

          let observed = null;
	          const observer = new MutationObserver(() => {
	            if (win.__mealmapForceScroll || stickToBottom) {
	              scrollBottom();
	              win.__mealmapForceScroll = false;
	              state.forceScroll = false;
	            }
	          });
	          state.observer = observer;

          function ensureObserver() {
            const panel = getActivePanel();
            if (!panel || panel === observed) return;
            try { observer.disconnect(); } catch (e) {}
            observed = panel;
            try { observer.observe(panel, { childList: true, subtree: true }); } catch (e) {}
          }

          let tries = 0;
          const iv = win.setInterval(() => {
            bindTabs();
            ensureObserver();
            wireAttachButton();
            tries += 1;
            if (tries > 60) win.clearInterval(iv);
          }, 250);
          state.interval = iv;

          win.addEventListener("resize", () => {
            try { wireAttachButton(); } catch (e) {}
          });
	        })();
	        </script>
	        """,
        height=0,
    )


inject_client_helpers()

if "_nav_tab" in st.session_state:
    idx = int(st.session_state.pop("_nav_tab"))
    components.html(
        f"""
        <script>
        (function () {{
          let win;
          try {{
            win = window.parent;
            void win.document;
          }} catch (e1) {{
            try {{
              win = window.top;
              void win.document;
            }} catch (e2) {{
              return;
            }}
          }}
          const doc = win.document;
          const tabs = Array.from(doc.querySelectorAll('button[data-baseweb="tab"]'));
          const i = {idx};
          try {{ win.localStorage.setItem('mealmap.activeTab', String(i)); }} catch (e) {{}}
          if (tabs[i]) tabs[i].click();
          setTimeout(() => {{ if (tabs[i]) tabs[i].click(); }}, 50);
        }})();
        </script>
        """,
        height=0,
    )

def request_scroll_to_bottom():
    st.session_state["_scroll_to_bottom"] = True


def maybe_scroll_to_bottom():
    if not st.session_state.pop("_scroll_to_bottom", False):
        return

    components.html(
        """
        <script>
        (function () {
          let win;
          try {
            win = window.parent;
            void win.document;
          } catch (e1) {
            try {
              win = window.top;
              void win.document;
            } catch (e2) {
              return;
            }
          }

	          try { win.__mealmapForceScroll = true; } catch (e) {}
	          function fallback() {
	            try { win.scrollTo({ top: win.document.body.scrollHeight, behavior: "smooth" }); } catch (e) {}
	          }
	          try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); else fallback(); } catch (e) { fallback(); }
	          setTimeout(() => { try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); else fallback(); } catch (e) { fallback(); } }, 50);
	          setTimeout(() => { try { if (win.__mealmapScrollBottom) win.__mealmapScrollBottom(); else fallback(); } catch (e) { fallback(); } }, 300);
	        })();
	        </script>
	        """,
        height=0,
    )


def apply_loaded_session(session_id: str):
    history = load_history()
    session = history.get(session_id)
    if not session:
        return

    st.session_state.meal_messages = session.get("meal_messages", [])
    st.session_state.shop_messages = session.get("shop_messages", [])
    st.session_state.current_session_id = session_id
    st.session_state.session_title = session.get("label", "Chat")
    st.session_state.location = session.get("location", st.session_state.get("location", ""))

    active_notes = session.get("active_notes") or ""
    if active_notes:
        st.session_state.active_notes = active_notes
        st.session_state.active_note_names = session.get("active_note_names", [])
    else:
        st.session_state.pop("active_notes", None)
        st.session_state.pop("active_note_names", None)
    request_scroll_to_bottom()
    set_query_sid(session_id)


if "load_session_id" in st.session_state:
    sid = st.session_state.pop("load_session_id")
    apply_loaded_session(sid)

if "_loaded_from_query" not in st.session_state:
    sid = get_query_sid()
    if sid:
        apply_loaded_session(sid)
    st.session_state["_loaded_from_query"] = True


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="mm-side-label">Your location</div>', unsafe_allow_html=True)
    location = st.text_input(
        "Your location",
        placeholder="e.g. 123 Main St, La Jolla CA 92037",
        help="A full address gives the most accurate nearby store distances.",
        key="location",
        label_visibility="collapsed",
    )

    st.markdown("---")

    st.markdown('<div class="mm-side-label">Navigation</div>', unsafe_allow_html=True)
    if st.button("• Meal planner", key="nav_meal", use_container_width=True):
        st.session_state["_nav_tab"] = 0
        st.rerun()
    if st.button("• Price finder", key="nav_shop", use_container_width=True):
        st.session_state["_nav_tab"] = 1
        st.rerun()

    st.markdown("---")

    # ── Chat history ──────────────────────────────────────────────────────────
    st.markdown('<div class="mm-side-label">Saved chats</div>', unsafe_allow_html=True)

    history = load_history()
    engine = _db_engine()
    if engine is not None and not history and os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f) or {}
        except Exception:
            legacy = {}
        if legacy:
            if st.button("Import legacy chats", use_container_width=True):
                uid = get_or_create_user_id()
                for sid, session in legacy.items():
                    try:
                        persistence.save_session(engine, uid, sid, session)
                    except Exception:
                        pass
                st.rerun()

    if history:
        for sid, session in sorted(history.items(), key=lambda x: x[1]["saved_at"], reverse=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(
                    session["label"],
                    key=f"load_{sid}",
                    use_container_width=True,
                ):
                    st.session_state.load_session_id = sid
                    st.rerun()
            with col2:
                if st.button("🗑️", key=f"del_{sid}"):
                    delete_session(sid)
                    st.rerun()
    else:
        st.caption("No saved chats yet.")

    st.markdown("---")

    if st.button("+  New chat", use_container_width=True):
        st.session_state.meal_messages = []
        st.session_state.shop_messages = []
        st.session_state.pop("current_session_id", None)
        st.session_state.pop("session_title", None)
        st.session_state.pop("active_notes", None)
        st.session_state.pop("active_note_names", None)
        st.session_state.pop("note_titles", None)
        set_query_sid(None)
        st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────

if "meal_messages" not in st.session_state:
    st.session_state.meal_messages = []
if "shop_messages" not in st.session_state:
    st.session_state.shop_messages = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def render_chat(messages: list):
    for msg in messages:
        with st.chat_message(msg["role"]):
            if isinstance(msg["content"], list):
                for part in msg["content"]:
                    if part["type"] == "text":
                        st.markdown(part["text"])
                    elif part["type"] == "image_url":
                        st.image(part["image_url"]["url"], width=300)
            else:
                st.markdown(msg["content"])

def render_empty_state():
    st.markdown(
        """
        <div class="mm-empty">
          <div class="mm-avatar">🥗</div>
          <div class="mm-hello">Hi, I’m <em>Maple</em> —<br/>your meal prep buddy.</div>
          <div class="mm-sub">
            Tell me what’s in your fridge and I’ll take the stress out of planning your week.
            Batch meals, smart shopping, best prices near you.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def clear_attachment(key_prefix: str):
    version_key = f"{key_prefix}_attach_version"
    st.session_state[version_key] = int(st.session_state.get(version_key, 0)) + 1


def render_attach_popover(key_prefix: str, *, allow_uploads: bool, default_is_grocery_list: bool):
    uploaded_image = None
    uploaded_pdf_text = ""
    image_is_grocery_list = default_is_grocery_list
    attach_version = int(st.session_state.get(f"{key_prefix}_attach_version", 0))
    upload_key = f"{key_prefix}_attach_any_{attach_version}"
    toggle_key = f"{key_prefix}_attach_is_list_{attach_version}"
    with st.popover(
        "Attach",
        icon=":material/add:",
        type="tertiary",
        key=f"{key_prefix}_attach_popover",
        help="Attach a photo or add Apple Notes",
    ):
        if allow_uploads:
            st.markdown("**Upload**")
            uploaded_any = st.file_uploader(
                "Upload",
                type=["jpg", "jpeg", "png", "webp", "pdf"],
                key=upload_key,
                label_visibility="collapsed",
            )
            image_is_grocery_list = st.toggle(
                "Treat image as grocery list",
                value=default_is_grocery_list,
                key=toggle_key,
            )

            if uploaded_any is not None:
                mime = getattr(uploaded_any, "type", "") or ""
                name = getattr(uploaded_any, "name", "") or ""
                is_pdf = "pdf" in mime.lower() or name.lower().endswith(".pdf")
                if is_pdf:
                    uploaded_pdf_text = documents.extract_text_from_pdf(uploaded_any)
                else:
                    uploaded_image = uploaded_any

            if uploaded_any is not None:
                if st.button(
                    "Use upload",
                    key=f"{key_prefix}_use_upload",
                    use_container_width=True,
                    help="Generate without typing",
                ):
                    st.session_state[f"{key_prefix}_submit_from_upload"] = True
                    st.rerun()

            if uploaded_any is not None and st.button(
                "Remove upload",
                key=f"{key_prefix}_remove_any",
                use_container_width=True,
            ):
                clear_attachment(key_prefix)
                st.rerun()
            st.markdown("---")

        if apple_notes.is_available():
            st.markdown("**Apple Notes**")
            if st.button(
                "Browse notes",
                use_container_width=True,
                key=f"{key_prefix}_browse_notes",
            ):
                with st.spinner("Loading notes..."):
                    st.session_state.note_titles = apple_notes.get_titles()

            if "note_titles" in st.session_state and st.session_state.note_titles:
                selected = st.multiselect(
                    "Select notes",
                    st.session_state.note_titles,
                    key=f"{key_prefix}_notes_select",
                )
                if st.button(
                    "Add selected notes",
                    use_container_width=True,
                    key=f"{key_prefix}_add_notes",
                ) and selected:
                    combined = [f"### {t}\n{apple_notes.get_content(t)}" for t in selected]
                    st.session_state.active_notes = "Here are my notes:\n\n" + "\n\n".join(combined)
                    st.session_state.active_note_names = selected
                    st.rerun()
            elif "note_titles" in st.session_state:
                st.info("No notes found.")

            if "active_notes" in st.session_state:
                st.success(f"Active: {', '.join(st.session_state.active_note_names)}")
                if st.button(
                    "Clear notes",
                    use_container_width=True,
                    key=f"{key_prefix}_clear_notes",
                ):
                    st.session_state.pop("active_notes", None)
                    st.session_state.pop("active_note_names", None)
                    st.rerun()

    return uploaded_image, uploaded_pdf_text, image_is_grocery_list


def handle_response(agent_gen) -> str:
    with st.chat_message("assistant"):
        placeholder = st.empty()
        final_response = ""
        status_line = st.empty()
        for event_type, event_data in agent_gen:
            if event_type == "status":
                status_line.caption(event_data)
            elif event_type == "done":
                final_response = event_data
        placeholder.markdown(final_response)
        status_line.empty()
        request_scroll_to_bottom()
        maybe_scroll_to_bottom()
    return final_response


def location_guard() -> bool:
    if not location:
        st.warning("Enter your location in the sidebar first.")
        return False
    return True


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_meal, tab_shop = st.tabs(["Meal planner", "Price finder"])


# ── Meal Planner ──────────────────────────────────────────────────────────────

with tab_meal:
    if not st.session_state.meal_messages:
        render_empty_state()
    else:
        st.caption("Tell me what ingredients you have and I'll plan batch meals for the week.")

    if "active_notes" in st.session_state:
        st.info(f"📝 Notes active: {', '.join(st.session_state.active_note_names)}")

    render_chat(st.session_state.meal_messages)

    st.markdown('<div data-mealmap-attach="meal"></div>', unsafe_allow_html=True)
    uploaded_image, uploaded_pdf_text, image_is_grocery_list = render_attach_popover(
        "meal",
        allow_uploads=True,
        default_is_grocery_list=False,
    )

    user_input = st.chat_input("What ingredients do you have?", key="meal_input")

    submit_from_upload = bool(st.session_state.pop("meal_submit_from_upload", False))
    if (user_input or submit_from_upload) and location_guard():
        client = get_groq_client()
        system_prompt = MEAL_PLAN_PROMPT.format(location=location)
        notes_context = st.session_state.get("active_notes", "")
        typed = user_input or "Create a batch meal prep plan from my attached list."

        if uploaded_pdf_text:
            with st.chat_message("user"):
                st.markdown(typed)
                st.markdown("**Attached list (PDF):**")
                st.code(uploaded_pdf_text[:4000])
            st.session_state.meal_messages.append({"role": "user", "content": typed})
            extracted = uploaded_pdf_text.strip()
            full_message = f"Grocery list (from PDF):\n{extracted}\n\n{typed}"

        elif uploaded_image:
            b64, mime = vision.encode_image(uploaded_image)
            data_url = f"data:{mime};base64,{b64}"

            with st.chat_message("user"):
                st.image(data_url, width=300)
                st.markdown(typed)

            st.session_state.meal_messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": typed},
                ],
            })

            with st.spinner("Analyzing image..."):
                if image_is_grocery_list:
                    extracted = vision.extract_grocery_list(client, b64, mime)
                    prefix = "Grocery list from image"
                else:
                    extracted = vision.analyze_ingredients(client, b64, mime)
                    prefix = "Ingredients from image"

            full_message = f"{prefix}: {extracted}\n\n" + typed
        else:
            with st.chat_message("user"):
                st.markdown(typed)
            st.session_state.meal_messages.append({"role": "user", "content": typed})
            full_message = typed

        if notes_context:
            full_message = f"{notes_context}\n\n{full_message}"

        response = handle_response(
            meal_agent.run(client, st.session_state.meal_messages[:-1], system_prompt, full_message)
        )
        st.session_state.meal_messages.append({"role": "assistant", "content": response})
        autosave(client)
        clear_attachment("meal")
        st.rerun()

    maybe_scroll_to_bottom()


# ── Price Finder ──────────────────────────────────────────────────────────────

with tab_shop:
    if not st.session_state.shop_messages:
        render_empty_state()
    else:
        st.caption("Find real prices from nearby stores for any ingredient.")
    if "active_notes" in st.session_state:
        st.info(f"📝 Notes active: {', '.join(st.session_state.active_note_names)}")

    render_chat(st.session_state.shop_messages)

    if not st.session_state.shop_messages:
        if st.button("Find cheapest bulking ingredients near me"):
            shop_input = "Find me the cheapest high-calorie bulking ingredients near me."
        else:
            shop_input = None
    else:
        shop_input = None

    st.markdown('<div data-mealmap-attach="shop"></div>', unsafe_allow_html=True)
    shop_uploaded_image, shop_uploaded_pdf_text, shop_image_is_list = render_attach_popover(
        "shop",
        allow_uploads=True,
        default_is_grocery_list=True,
    )

    submit_from_upload = bool(st.session_state.pop("shop_submit_from_upload", False))
    typed_input = st.chat_input("e.g. 'tomato paste, rice, eggs'", key="shop_input")
    if typed_input:
        shop_input = typed_input

    if (not shop_input and (submit_from_upload or shop_uploaded_pdf_text)) and shop_uploaded_pdf_text:
        shop_input = f"Grocery list (from PDF):\n{shop_uploaded_pdf_text.strip()}"
    elif (not shop_input and (submit_from_upload or shop_uploaded_image)) and shop_uploaded_image:
        client = get_groq_client()
        b64, mime = vision.encode_image(shop_uploaded_image)
        extracted = (
            vision.extract_grocery_list(client, b64, mime)
            if shop_image_is_list
            else vision.analyze_ingredients(client, b64, mime)
        )
        shop_input = f"Grocery list (from image):\n{extracted.strip()}"

    if shop_input and location_guard():
        client = get_groq_client()
        notes_context = st.session_state.get("active_notes", "")
        full_message = f"{notes_context}\n\n{shop_input}" if notes_context else shop_input

        with st.chat_message("user"):
            st.markdown(shop_input)
        st.session_state.shop_messages.append({"role": "user", "content": shop_input})

        kroger_id = st.secrets.get("KROGER_CLIENT_ID", "")
        kroger_secret = st.secrets.get("KROGER_CLIENT_SECRET", "")

        response = handle_response(shopping_agent.run(client, location, full_message, kroger_id, kroger_secret))
        st.session_state.shop_messages.append({"role": "assistant", "content": response})
        autosave(client)
        clear_attachment("shop")
        st.rerun()

    maybe_scroll_to_bottom()
