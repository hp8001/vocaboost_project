import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';

/// Smoke test kết nối Firebase — chỉ chạy ở chế độ debug (kDebugMode).
///
/// In ra số lượng [FirebaseApp] đã khởi tạo và projectId của app mặc định
/// ([Firebase.app]().options.projectId). Không hard-code khóa hay ID nào.
void runFirebaseSmokeTest() {
  if (!kDebugMode) {
    return;
  }

  final apps = Firebase.apps;
  debugPrint('[Firebase smoke test] Số lượng Firebase.apps: ${apps.length}');

  final options = Firebase.app().options;
  debugPrint('[Firebase smoke test] projectId: ${options.projectId}');
}
