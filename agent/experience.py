"""Durable experience retrieval. This is not a model-weight trainer."""
import json
from pathlib import Path
import re
import sqlite3
import uuid


class ExperienceLibrary:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "experience.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP, world TEXT,
            objective TEXT, verified INTEGER, lesson TEXT, trace TEXT, result TEXT)""")
        self.db.commit()

    def save(self, world, objective, result, trace, lesson):
        key = str(uuid.uuid4())
        self.db.execute("INSERT INTO episodes(id,world,objective,verified,lesson,trace,result) VALUES(?,?,?,?,?,?,?)",
                        (key, world, objective, int(bool(result.get("success") and result.get("verified"))),
                         lesson[:3000], json.dumps(trace), json.dumps(result)))
        self.db.commit()
        return key

    def recall(self, objective, limit=5):
        words = set(re.findall(r"[a-z_]{3,}", objective.lower()))
        rows = self.db.execute("SELECT objective,verified,lesson,trace,world FROM episodes ORDER BY created DESC,rowid DESC LIMIT 100").fetchall()
        rows = sorted(rows, key=lambda r: len(words & set(re.findall(r"[a-z_]{3,}", (r["objective"] + ' ' + r["lesson"]).lower()))), reverse=True)
        return [{"objective": row["objective"], "verified": bool(row["verified"]), "lesson": row["lesson"],
                 "tools_used": [step.get("action", {}).get("name") for step in json.loads(row["trace"]) if step.get("action")],
                 "historical_world": row["world"]} for row in rows[:limit]]

    def close(self):
        self.db.close()

    def progress(self, limit=12):
        """Observed coverage, not a claim that any capability is mastered."""
        rows = self.db.execute('''SELECT objective, COUNT(*) AS attempts, SUM(verified) AS confirmed,
                                 MAX(created) AS last_attempt FROM episodes
                                 GROUP BY LOWER(TRIM(objective)) ORDER BY last_attempt DESC LIMIT ?''', (limit,)).fetchall()
        return [{'objective': row['objective'], 'attempts': row['attempts'], 'confirmed': row['confirmed'],
                 'unfinished': row['attempts'] - row['confirmed']} for row in rows]

    def update(self, key, lesson, result):
        self.db.execute('UPDATE episodes SET lesson=?,result=?,verified=? WHERE id=?',
                        (lesson, json.dumps(result), int(bool(result.get('success') and result.get('verified'))), key))
        self.db.commit()
