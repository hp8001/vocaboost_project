#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VocaBoost — Đồng bộ bảng xếp hạng (QĐ3, 13/11/2026)

Ứng dụng KHÔNG ghi điểm bảng xếp hạng. Script này (chạy ngoài ứng dụng, dùng Admin SDK nên bỏ qua
Security Rules) làm các việc sau mỗi lần chạy:
  1. Với từng người dùng chưa bị khóa, đọc các phiên học users/{uid}/sessions của TUẦN HIỆN TẠI
     (từ 00:00 thứ Hai theo UTC+7).
  2. Tính XP "đã xác thực" của từng phiên (không tin xpEarned do app gửi): bị chặn trên theo số từ,
     số lần đúng/sai, loại phiên và thời lượng; tổng tuần bị chặn bởi WEEKLY_CAP.
  3. Ghi leaderboard/{uid} (uid, displayName, photoUrl, weeklyXP, weeklyXPResetAt) cho người có điểm > 0,
     cập nhật users.weeklyXP / users.weeklyXPResetAt, và xóa bản ghi leaderboard không còn hợp lệ
     (tuần cũ, người bị khóa, hết điểm).

Chỉ ghi khi giá trị thay đổi, nên tốn rất ít lượt ghi của gói Spark. Chạy lại bao nhiêu lần cũng an toàn.

Cách dùng:
  python tools/leaderboard_sync.py --self-test                 # kiểm thử logic thuần, không cần khóa
  python tools/leaderboard_sync.py --dry-run --key KHOA.json   # tính và in kết quả, KHÔNG ghi gì
  python tools/leaderboard_sync.py --key KHOA.json             # ghi thật
Khóa service account có thể cấp bằng: --key <file>, biến môi trường FIREBASE_SERVICE_ACCOUNT_JSON
(nội dung JSON; dùng trong GitHub Actions) hoặc GOOGLE_APPLICATION_CREDENTIALS (đường dẫn file).
KHÔNG bao giờ commit khóa lên Git và không in nội dung khóa ra log.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# ---------- Tham số (phải khớp plan > xp_gamification.leaderboard và firestore.rules) ----------
TZ_OFFSET_HOURS = 7          # UTC+7
WEEKLY_CAP = 5000            # trần điểm tuần của một người
MAX_WORDS_PER_SESSION = 100
MIN_SECONDS_PER_WORD = 2     # phiên nhanh hơn mức này không được tính điểm
XP_LEARN_PER_WORD = 10       # learn_new_word
XP_REVIEW_CORRECT_MAX = 8    # review_correct_easy (mức cao nhất của ôn đúng)
XP_REVIEW_INCORRECT = 1      # review_incorrect
XP_PRONUNCIATION_PER_WORD = 15
FUTURE_TOLERANCE = timedelta(minutes=5)
UTC = timezone.utc


# ---------- Logic thuần (không dùng Firebase, có self-test) ----------
def week_bounds(now_utc):
    """Trả (đầu tuần, đầu tuần kế tiếp) theo UTC+7, đổi về UTC (datetime có tzinfo)."""
    tz = timedelta(hours=TZ_OFFSET_HOURS)
    local = now_utc.astimezone(UTC) + tz
    monday_local = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = monday_local - tz
    return start_utc, start_utc + timedelta(days=7)


def verified_xp(s):
    """XP của một phiên sau khi bị chặn trên. Phiên bất thường -> 0."""
    try:
        stype = s.get('sessionType')
        words = int(s.get('wordsStudied', 0))
        correct = int(s.get('correctCount', 0))
        incorrect = int(s.get('incorrectCount', 0))
        claimed = int(s.get('xpEarned', 0))
        duration = int(s.get('durationSeconds', 0))
    except (TypeError, ValueError):
        return 0
    if not (1 <= words <= MAX_WORDS_PER_SESSION):
        return 0
    if correct < 0 or incorrect < 0 or claimed < 0:
        return 0
    if duration < words * MIN_SECONDS_PER_WORD:
        return 0
    if stype == 'learn':
        bound = words * XP_LEARN_PER_WORD
    elif stype == 'review':
        bound = min(correct * XP_REVIEW_CORRECT_MAX + incorrect * XP_REVIEW_INCORRECT, words * XP_REVIEW_CORRECT_MAX)
    elif stype == 'pronunciation':
        bound = words * XP_PRONUNCIATION_PER_WORD
    else:
        return 0
    return max(0, min(claimed, bound))


def weekly_total(sessions, now_utc):
    """Tổng điểm tuần của một người từ danh sách phiên (dict), đã chặn bởi WEEKLY_CAP."""
    start, nxt = week_bounds(now_utc)
    total = 0
    for s in sessions:
        d = s.get('date')
        if not isinstance(d, datetime):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        if d < start or d >= nxt or d > now_utc + FUTURE_TOLERANCE:
            continue
        total += verified_xp(s)
    return min(total, WEEKLY_CAP)


def build_plan(users, sessions_by_uid, existing_lb, existing_users_weekly, now_utc):
    """
    users: {uid: {displayName, photoUrl, isDisabled}}
    sessions_by_uid: {uid: [session dict]}
    existing_lb: {uid: dict}  (leaderboard hiện có)
    existing_users_weekly: {uid: (weeklyXP, weeklyXPResetAt)}
    Trả: (lb_set {uid: doc}, lb_delete [uid], users_update {uid: {weeklyXP, weeklyXPResetAt}})
    """
    _, nxt = week_bounds(now_utc)
    lb_set, lb_delete, users_update = {}, [], {}
    for uid, u in users.items():
        total = 0 if u.get('isDisabled') is True else weekly_total(sessions_by_uid.get(uid, []), now_utc)
        if total > 0:
            doc = {
                'uid': uid,
                'displayName': u.get('displayName') or 'Người học',
                'photoUrl': u.get('photoUrl'),
                'weeklyXP': total,
                'weeklyXPResetAt': nxt,
            }
            if existing_lb.get(uid) != doc:
                lb_set[uid] = doc
        cur = existing_users_weekly.get(uid)
        if cur is None or cur[0] != total or cur[1] != nxt:
            users_update[uid] = {'weeklyXP': total, 'weeklyXPResetAt': nxt}
    keep = {uid for uid, u in users.items()
            if u.get('isDisabled') is not True and weekly_total(sessions_by_uid.get(uid, []), now_utc) > 0}
    for uid in existing_lb:
        if uid not in keep:
            lb_delete.append(uid)
    return lb_set, lb_delete, users_update


def self_test():
    # Biên tuần: 13/11/2026 07:55 (UTC+7, thứ Sáu) -> thứ Hai 09/11 00:00 (UTC+7) = 08/11 17:00 UTC
    now = datetime(2026, 11, 13, 0, 55, tzinfo=UTC)
    s, n = week_bounds(now)
    assert s == datetime(2026, 11, 8, 17, 0, tzinfo=UTC), s
    assert n == datetime(2026, 11, 15, 17, 0, tzinfo=UTC), n
    # Thứ Hai 00:30 (UTC+7) vẫn thuộc tuần mới
    s2, _ = week_bounds(datetime(2026, 11, 8, 17, 30, tzinfo=UTC))
    assert s2 == datetime(2026, 11, 8, 17, 0, tzinfo=UTC)
    # Chủ nhật 23:59 (UTC+7) vẫn thuộc tuần cũ
    s3, n3 = week_bounds(datetime(2026, 11, 15, 16, 59, tzinfo=UTC))
    assert s3 == datetime(2026, 11, 8, 17, 0, tzinfo=UTC) and n3 == datetime(2026, 11, 15, 17, 0, tzinfo=UTC)

    base = {'sessionType': 'learn', 'wordsStudied': 10, 'correctCount': 10, 'incorrectCount': 0, 'xpEarned': 100, 'durationSeconds': 120}
    assert verified_xp(base) == 100
    assert verified_xp({**base, 'xpEarned': 9999}) == 100                      # chặn xpEarned khai quá
    assert verified_xp({**base, 'durationSeconds': 10}) == 0                   # quá nhanh (< 2 giây/từ)
    assert verified_xp({**base, 'wordsStudied': 101, 'durationSeconds': 999}) == 0
    assert verified_xp({**base, 'sessionType': 'hack'}) == 0
    assert verified_xp({**base, 'xpEarned': -5}) == 0
    rv = {'sessionType': 'review', 'wordsStudied': 10, 'correctCount': 6, 'incorrectCount': 4, 'xpEarned': 500, 'durationSeconds': 60}
    assert verified_xp(rv) == 6 * 8 + 4 * 1                                    # = 52
    pr = {'sessionType': 'pronunciation', 'wordsStudied': 5, 'correctCount': 5, 'incorrectCount': 0, 'xpEarned': 75, 'durationSeconds': 60}
    assert verified_xp(pr) == 75
    assert verified_xp({'sessionType': 'learn'}) == 0                          # thiếu trường

    in_week = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    old = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    future = now + timedelta(hours=2)
    sess = [{**base, 'date': in_week}, {**base, 'date': old}, {**base, 'date': future}, {**base, 'date': None}]
    assert weekly_total(sess, now) == 100                                      # chỉ phiên trong tuần, không tương lai
    assert weekly_total([{**base, 'date': in_week, 'xpEarned': 100}] * 100, now) == WEEKLY_CAP   # trần tuần

    users = {
        'a': {'displayName': 'An', 'photoUrl': None},
        'b': {'displayName': 'Bình', 'photoUrl': 'av1', 'isDisabled': True},
        'c': {'displayName': 'Chi', 'photoUrl': None},
    }
    sbu = {'a': [{**base, 'date': in_week}], 'b': [{**base, 'date': in_week}], 'c': []}
    existing_lb = {'b': {'uid': 'b'}, 'c': {'uid': 'c'}, 'zz': {'uid': 'zz'}}
    lb_set, lb_del, up = build_plan(users, sbu, existing_lb, {'a': (0, n)}, now)
    assert list(lb_set) == ['a'] and lb_set['a']['weeklyXP'] == 100 and lb_set['a']['weeklyXPResetAt'] == n
    assert sorted(lb_del) == ['b', 'c', 'zz']                                  # khóa / hết điểm / mồ côi
    assert up['a']['weeklyXP'] == 100 and up['b']['weeklyXP'] == 0 and up['c']['weeklyXP'] == 0
    # Chạy lại lần 2 với trạng thái đã đồng bộ -> không còn gì để ghi
    existing_lb2 = {'a': lb_set['a']}
    lb_set2, lb_del2, up2 = build_plan(users, sbu, existing_lb2, {'a': (100, n), 'b': (0, n), 'c': (0, n)}, now)
    assert not lb_set2 and not lb_del2 and not up2
    print('self-test: OK')


# ---------- Phần ghi/đọc Firestore ----------
def init_firestore(key_path):
    import firebase_admin
    from firebase_admin import credentials, firestore
    env_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    if key_path:
        cred = credentials.Certificate(key_path)
    elif env_json:
        cred = credentials.Certificate(json.loads(env_json))
    elif os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        cred = credentials.Certificate(os.environ['GOOGLE_APPLICATION_CREDENTIALS'])
    else:
        sys.exit('Thiếu khóa: dùng --key, FIREBASE_SERVICE_ACCOUNT_JSON hoặc GOOGLE_APPLICATION_CREDENTIALS.')
    firebase_admin.initialize_app(cred)
    return firestore.client()


def run(db, dry_run):
    from google.cloud.firestore_v1.base_query import FieldFilter
    now = datetime.now(UTC)
    start, nxt = week_bounds(now)
    users, users_weekly = {}, {}
    for d in db.collection('users').stream():
        x = d.to_dict() or {}
        users[d.id] = {'displayName': x.get('displayName'), 'photoUrl': x.get('photoUrl'), 'isDisabled': x.get('isDisabled')}
        users_weekly[d.id] = (x.get('weeklyXP'), x.get('weeklyXPResetAt'))
    sessions_by_uid = {}
    for uid in users:
        q = db.collection('users').document(uid).collection('sessions').where(filter=FieldFilter('date', '>=', start))
        sessions_by_uid[uid] = [s.to_dict() or {} for s in q.stream()]
    existing_lb = {d.id: (d.to_dict() or {}) for d in db.collection('leaderboard').stream()}

    lb_set, lb_del, up = build_plan(users, sessions_by_uid, existing_lb, users_weekly, now)
    ranked = sorted(((d['weeklyXP'], uid) for uid, d in
                     {**{u: e for u, e in existing_lb.items() if u not in lb_del}, **lb_set}.items()), reverse=True)
    print(f'Tuần {start.isoformat()} -> {nxt.isoformat()} | người dùng: {len(users)} | '
          f'ghi leaderboard: {len(lb_set)} | xóa: {len(lb_del)} | cập nhật users: {len(up)}')
    for i, (xp, uid) in enumerate(ranked[:5], 1):
        print(f'  top {i}: {uid[:6]}… {xp} XP')
    if dry_run:
        print('--dry-run: KHÔNG ghi gì vào Firestore.')
        return
    ops = ([('set', db.collection('leaderboard').document(uid), d) for uid, d in lb_set.items()]
           + [('delete', db.collection('leaderboard').document(uid), None) for uid in lb_del]
           + [('update', db.collection('users').document(uid), d) for uid, d in up.items()])
    for i in range(0, len(ops), 400):
        batch = db.batch()
        for kind, ref, data in ops[i:i + 400]:
            if kind == 'set':
                batch.set(ref, data)
            elif kind == 'update':
                batch.update(ref, data)
            else:
                batch.delete(ref)
        batch.commit()
    print(f'Đã đồng bộ leaderboard: {len([1 for _ in ranked])} người có điểm tuần này.')


def main():
    ap = argparse.ArgumentParser(description='Đồng bộ bảng xếp hạng VocaBoost')
    ap.add_argument('--key', help='Đường dẫn file khóa service account (JSON)')
    ap.add_argument('--dry-run', action='store_true', help='Chỉ tính và in, không ghi')
    ap.add_argument('--self-test', action='store_true', help='Chạy kiểm thử logic thuần rồi thoát')
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    run(init_firestore(a.key), a.dry_run)


if __name__ == '__main__':
    main()
