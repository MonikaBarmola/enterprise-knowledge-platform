import streamlit as st
from auth import authenticate_user, setup_database
from rag import retrieve, answer_question, log_query, NotIngestedError

st.set_page_config(page_title="Enterprise Knowledge Platform", page_icon="🔐")

setup_database()

st.title("🔐 Enterprise Knowledge Platform")
st.caption("Secure RAG with retrieval-level RBAC")

if "user" not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.subheader("Login")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        user = authenticate_user(username, password)
        if user:
            st.session_state.user = user
            st.rerun()
        st.error("Invalid username or password.")

    st.info("Use the setup mode below to create a demo account.")
    if st.button("Create demo users"):
        setup_database(create_demo_users=True)
        st.success("Demo users created. Try: engineer / engineer123")
    st.stop()

user = st.session_state.user

with st.sidebar:
    st.success(f"Logged in: {user['username']}")
    st.write(f"Role: **{user['role']}**")
    st.write("Allowed departments:")
    for dept in user["allowed_departments"]:
        st.write(f"- {dept}")

    if st.button("Log out"):
        st.session_state.user = None
        st.rerun()

question = st.text_area(
    "Ask a question",
    placeholder="Example: What is the engineering deployment process?"
)

if st.button("Ask", type="primary", disabled=not question.strip()):
    try:
        chunks = retrieve(question, user["allowed_departments"])
    except NotIngestedError as e:
        st.error(str(e))
        st.stop()

    if not chunks:
        answer = "I couldn't find any information you are authorized to access for this question."
        sources = []
        st.warning(answer)
    else:
        answer, sources = answer_question(question, chunks)

        st.subheader("Answer")
        st.write(answer)

        if sources:
            st.subheader("Sources")
            for source in sources:
                st.write(f"- {source}")

    log_query(
        user["username"],
        user["role"],
        question,
        user["allowed_departments"],
        sources,
        len(chunks),
    )
