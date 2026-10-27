"""最小复现：st.pills 写 session_state 给「表单内 text_input」在提交后失效吗？

两种变体（?v=form / ?v=plain）各跑一遍，用来判断根因是 st.form 还是别的。
"""
import streamlit as st

EX = ["问题甲", "问题乙", "问题丙"]
v = st.query_params.get("v", "form")

for k, d in (("qa_input", ""), ("prev", ""), ("seq", 0), ("need_clear", False),
             ("pills", None), ("submits", 0)):
    if k not in st.session_state:
        st.session_state[k] = d

if st.session_state.pop("need_clear", False):
    st.session_state["qa_input"] = ""
    st.session_state["prev"] = ""
    st.session_state["pills"] = None
    st.session_state["seq"] = int(st.session_state["seq"]) + 1

st.write("variant = **%s** · seq = %s · submits = %s"
         % (v, st.session_state["seq"], st.session_state["submits"]))

sel = st.pills("示例", EX, key="pills", selection_mode="single", default=None)
if sel and sel != st.session_state["prev"]:
    st.session_state["qa_input"] = sel
    st.session_state["prev"] = sel

if v == "form":
    with st.form("f_%d" % int(st.session_state["seq"]), clear_on_submit=True):
        q = st.text_input("问题", key="qa_input")
        go = st.form_submit_button("提交")
else:
    q = st.text_input("问题", key="qa_input")
    go = st.button("提交")

st.caption("提交值 = %r" % (q,))
if go:
    st.session_state["submits"] = int(st.session_state["submits"]) + 1
    st.session_state["need_clear"] = True
    st.rerun()
