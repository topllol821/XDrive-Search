import queue
import sqlite3
import threading
import time

from fast_indexer import build_index_fast


class FakeIndexer:
    def __init__(self, db_path):
        self.db_path = db_path
        self._stop = threading.Event()
        self.progress_q = queue.Queue()

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE files(
                id INTEGER PRIMARY KEY,
                name TEXT, name_lower TEXT,
                path TEXT, dir TEXT,
                ext TEXT, size INTEGER, mtime REAL
            )
            """
        )
        cur.execute(
            """
            CREATE VIRTUAL TABLE files_fts USING fts5(
                name, path, content='files', content_rowid='id', tokenize='trigram'
            )
            """
        )
        cur.execute(
            "CREATE TRIGGER files_ai AFTER INSERT ON files "
            "BEGIN INSERT INTO files_fts(rowid,name,path) VALUES (new.id,new.name,new.path); END"
        )
        cur.execute(
            "CREATE TRIGGER files_ad AFTER DELETE ON files "
            "BEGIN INSERT INTO files_fts(files_fts,rowid,name,path) "
            "VALUES('delete',old.id,old.name,old.path); END"
        )
        cur.execute(
            "CREATE TRIGGER files_au AFTER UPDATE ON files "
            "BEGIN "
            "INSERT INTO files_fts(files_fts,rowid,name,path) "
            "VALUES('delete',old.id,old.name,old.path); "
            "INSERT INTO files_fts(rowid,name,path) VALUES (new.id,new.name,new.path); "
            "END"
        )
        con.commit()
        con.close()


def test_full_then_incremental_add_update_delete(tmp_path):
    root = tmp_path / "share"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir()

    (root / "A" / "part1.sldprt").write_text("one", encoding="utf-8")
    (root / "A" / "part2.dwg").write_text("two", encoding="utf-8")
    (root / "B" / "readme.pdf").write_text("pdf", encoding="utf-8")

    db_path = tmp_path / "index.db"
    indexer = FakeIndexer(str(db_path))

    build_index_fast(indexer, str(root), incremental=False)

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
    con.close()

    time.sleep(0.02)
    (root / "A" / "part1.sldprt").write_text("one changed", encoding="utf-8")
    (root / "A" / "part2.dwg").unlink()
    (root / "B" / "new.step").write_text("new", encoding="utf-8")

    build_index_fast(indexer, str(root), incremental=True)

    con = sqlite3.connect(db_path)
    names = {row[0] for row in con.execute("SELECT name FROM files")}
    assert names == {"part1.sldprt", "readme.pdf", "new.step"}
    assert con.execute(
        "SELECT COUNT(*) FROM files_fts WHERE files_fts MATCH 'part1'"
    ).fetchone()[0] == 1
    con.close()


def test_blacklist_is_not_indexed(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    (root / "good.dwg").write_text("ok", encoding="utf-8")
    (root / "~$temp.dwg").write_text("temp", encoding="utf-8")
    (root / "Thumbs.db").write_text("junk", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "hidden.txt").write_text("hidden", encoding="utf-8")

    db_path = tmp_path / "index.db"
    indexer = FakeIndexer(str(db_path))
    build_index_fast(indexer, str(root), incremental=False)

    con = sqlite3.connect(db_path)
    names = {row[0] for row in con.execute("SELECT name FROM files")}
    con.close()
    assert names == {"good.dwg"}
