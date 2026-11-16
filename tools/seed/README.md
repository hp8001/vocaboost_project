# Seed dữ liệu VocaBoost

Dữ liệu: topics.json (10 chủ đề), vocab_seed.csv (100 từ), badges.json (10 huy hiệu, lấy từ master_plan_v33.json).

    cd tools/seed
    python seed_firestore.py --dry-run                                   # chỉ kiểm tra, không ghi gì
    pip install -r requirements.txt
    python seed_firestore.py --key ../secrets/serviceAccountKey.json     # nạp thật

- KHÔNG commit thư mục tools/secrets/ hay file serviceAccountKey*.json.
- Chạy lại an toàn: ID tài liệu cố định (vd. family_mother) nên chỉ ghi đè.
- IPA theo tiếng Anh Mỹ (khớp giọng TTS en-US). Mức CEFR là ước lượng, chưa đối chiếu danh sách chính thức.
