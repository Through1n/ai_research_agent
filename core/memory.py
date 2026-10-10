"""记忆管理 Memory：短期对话（Agent 内）+ 长期笔记/画像（持久化 JSON）+ 检索记忆（向量库）。"""
import json
import re
from pathlib import Path


class Memory:
    def __init__(self, memory_path: Path, profile_path: Path, user="user_001"):
        self.memory_path = Path(memory_path)
        self.profile_path = Path(profile_path)
        self.user = user
        self.data = self._load(self.memory_path, {"notes": [], "kv": {}})
        self.profiles = self._load(self.profile_path, {})

    @staticmethod
    def _load(path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return json.loads(json.dumps(default))

    def _save(self):
        self.memory_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_profile(self):
        self.profile_path.write_text(json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 长期键值 / 笔记 ----
    def remember(self, key, value):
        self.data["kv"][key] = value
        self._save()

    def recall(self, key, default=None):
        return self.data["kv"].get(key, default)

    def add_note(self, text):
        text = text.strip()
        if text:
            self.data["notes"].append({"user": self.user, "text": text})
            self._save()

    def relevant_notes(self, query, top=3):
        q = set(re.findall(r"\w+", query.lower()))
        scored = []
        for n in self.data["notes"]:
            t = set(re.findall(r"\w+", n["text"].lower()))
            overlap = len(q & t)
            if overlap:
                scored.append((overlap, n["text"]))
        return [t for _, t in sorted(scored, reverse=True)[:top]]

    # ---- 用户画像 ----
    def update_profile(self, paper_name):
        self.profiles.setdefault(self.user, {})
        domain = "通用"
        pn = paper_name.lower()
        if any(k in pn for k in ["bert", "transformer", "gpt", "attention", "llm"]):
            domain = "NLP / 大模型"
        elif any(k in pn for k in ["image", "vision", "cnn"]):
            domain = "计算机视觉"
        elif any(k in pn for k in ["bio", "medical", "protein", "clinical"]):
            domain = "生物医学"
        elif any(k in pn for k in ["graph", "gnn"]):
            domain = "图神经网络"
        self.profiles[self.user][domain] = self.profiles[self.user].get(domain, 0) + 1
        self._save_profile()
        return domain

    def profile(self):
        return self.profiles.get(self.user, {})
