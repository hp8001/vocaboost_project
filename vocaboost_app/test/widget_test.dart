import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vocaboost_app/main.dart';

void main() {
  testWidgets('Splash screen shows app name', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: VocaBoostApp()));
    await tester.pump();
    expect(find.text('VocaBoost'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}