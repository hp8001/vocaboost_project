#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nạp dữ liệu ban đầu của VocaBoost lên Cloud Firestore.

Nạp: topics, vocabularies, badges_master (đúng tên collection/field trong master_plan_v33.json).
An toàn khi chạy lại: ID tài liệu cố định => chạy lại chỉ GHI ĐÈ, không tạo bản sao.

Cách dùng (chạy trong thư mục tools/seed):
  1) Chạy thử, chỉ kiểm tra dữ liệu, KHÔNG cần khóa, KHÔNG ghi gì:
       python seed_firestore.py --dry-run
  2) Nạp thật (cần khóa service account, TUYỆT ĐỐI không commit khóa này lên git):
       python seed_firestore.py --key ../secrets/serviceAccountKey.json
"""
import argparse, csv, json, re, sys, unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
CEFR = {"A1", "A2", "B1"}
POS = {"n", "v", "adj", "adv"}                      # theo plan: partOfSpeech n/v/adj/adv
REQUIRED_COLS = ["topicId", "word", "partOfSpeech", "cefr_level", "ipa", "meaning", "exampleEn", "exampleVi"]


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")


def load_topics():
    topics = json.loads((HERE / "topics.json").read_text(encoding="utf-8"))
    ids = [t["id"] for t in topics]
    if len(ids) != len(set(ids)):
        sys.exit("LỖI: topics.json có id trùng nhau")
    return topics


def load_badges():
    return json.loads((HERE / "badges.json").read_text(encoding="utf-8"))


def load_vocab(topic_ids):
    errors, rows, seen = [], [], set()
    with open(HERE / "vocab_seed.csv", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLS if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"LỖI: vocab_seed.csv thiếu cột: {missing}")
        for n, r in enumerate(reader, start=2):          # dòng 1 là tiêu đề
            r = {k: (v or "").strip() for k, v in r.items()}
            where = f"dòng {n} ({r.get('word','?')})"
            for c in REQUIRED_COLS:
                if not r[c]:
                    errors.append(f"{where}: cột '{c}' đang trống")
            if r["topicId"] not in topic_ids:
                errors.append(f"{where}: topicId '{r['topicId']}' không có trong topics.json")
            if r["cefr_level"] not in CEFR:
                errors.append(f"{where}: cefr_level '{r['cefr_level']}' không thuộc {sorted(CEFR)}")
            if r["partOfSpeech"] not in POS:
                errors.append(f"{where}: partOfSpeech '{r['partOfSpeech']}' không thuộc {sorted(POS)}")
            if not (r["ipa"].startswith("/") and r["ipa"].endswith("/") and len(r["ipa"]) >= 3):
                errors.append(f"{where}: ipa phải có dạng /.../ (đang là '{r['ipa']}')")
            doc_id = f"{r['topicId']}_{slug(r['word'])}"
            if doc_id in seen:
                errors.append(f"{where}: trùng từ trong cùng chủ đề (id {doc_id})")
            seen.add(doc_id)
            rows.append((doc_id, r))
    if errors:
        print("PHÁT HIỆN LỖI DỮ LIỆU:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    return rows


def build_docs(topics, vocab_rows, badges):
    counts = {}
    for _, r in vocab_rows:
        counts[r["topicId"]] = counts.get(r["topicId"], 0) + 1
    topic_docs = [(t["id"], {
        "id": t["id"], "nameVi": t["nameVi"], "nameEn": t["nameEn"], "icon": t["icon"],
        "totalWords": counts.get(t["id"], 0), "order": t["order"], "isActive": True,
    }) for t in topics]
    vocab_docs = [(doc_id, {
        "id": doc_id, "word": r["word"], "meaning": r["meaning"], "ipa": r["ipa"],
        "exampleEn": r["exampleEn"], "exampleVi": r["exampleVi"], "topicId": r["topicId"],
        "partOfSpeech": r["partOfSpeech"], "cefr_level": r["cefr_level"], "isActive": True,
    }) for doc_id, r in vocab_rows]
    badge_docs = [(b["id"], dict(b)) for b in badges]
    return topic_docs, vocab_docs, badge_docs, counts


def commit(db, collection, docs):
    batch, n = db.batch(), 0
    for doc_id, data in docs:
        batch.set(db.collection(collection).document(doc_id), data)
        n += 1
        if n % 400 == 0:                                 # giới hạn batch của Firestore là 500
            batch.commit()
            batch = db.batch()
    batch.commit()
    print(f"  ✔ {collection}: đã ghi {len(docs)} tài liệu")


def main():
    ap = argparse.ArgumentParser(description="Seed dữ liệu VocaBoost lên Firestore")
    ap.add_argument("--key", help="đường dẫn file serviceAccountKey.json (KHÔNG commit file này)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra dữ liệu, không ghi gì")
    args = ap.parse_args()

    topics = load_topics()
    vocab_rows = load_vocab({t["id"] for t in topics})
    topic_docs, vocab_docs, badge_docs, counts = build_docs(topics, vocab_rows, load_badges())

    print(f"Dữ liệu hợp lệ: {len(topic_docs)} chủ đề, {len(vocab_docs)} từ vựng, {len(badge_docs)} huy hiệu")
    for t in topics:
        print(f"   - {t['id']:<11} {counts.get(t['id'], 0):>3} từ")

    if args.dry_run:
        print("\n[dry-run] Không ghi gì lên Firestore.")
        return
    if not args.key:
        sys.exit("Thiếu --key. Dùng --dry-run để chỉ kiểm tra, hoặc --key <serviceAccountKey.json> để nạp thật.")

    import firebase_admin                                # import muộn: chế độ --dry-run không cần cài thư viện
    from firebase_admin import credentials, firestore
    firebase_admin.initialize_app(credentials.Certificate(args.key))
    db = firestore.client()
    print("\nĐang ghi lên Firestore...")
    commit(db, "topics", topic_docs)
    commit(db, "vocabularies", vocab_docs)
    commit(db, "badges_master", badge_docs)
    print("Xong. Mở Firebase Console > Firestore Database để kiểm tra.")


if __name__ == "__main__":
    main()
