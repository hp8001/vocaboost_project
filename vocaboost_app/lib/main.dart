import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/dev/firebase_smoke_test.dart';
import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';
import 'firebase_options.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
  runFirebaseSmokeTest();
  runApp(const ProviderScope(child: VocaBoostApp()));
}

class VocaBoostApp extends StatelessWidget {
  const VocaBoostApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'VocaBoost',
      theme: AppTheme.light(),
      routerConfig: AppRouter.router,
    );
  }
}