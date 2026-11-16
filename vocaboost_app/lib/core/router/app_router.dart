import 'package:go_router/go_router.dart';
import '../../features/auth/presentation/screens/splash_screen.dart';

abstract final class AppRouter {
  static final GoRouter router = GoRouter(
    routes: [
      GoRoute(
        path: '/',
        builder: (_, _) => const SplashScreen(),
      ),
    ],
  );
}