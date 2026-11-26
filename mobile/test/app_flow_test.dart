import 'package:flutter_test/flutter_test.dart';
import 'package:laqeet/core/api_client.dart';
import 'package:laqeet/core/repository.dart';
import 'package:laqeet/core/token_store.dart';
import 'package:laqeet/main.dart';
import 'package:laqeet/state/session.dart';
import 'package:material_ui/material_ui.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_server.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  Future<(FakeServer, Session)> boot(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 2.5;
    addTearDown(tester.view.reset);
    final server = FakeServer();
    final tokens = MemoryTokenStore();
    final session = Session(repo: Repository(ApiClient(tokens: tokens, httpClient: server.client, baseUrl: 'http://x')), tokens: tokens);
    await tester.pumpWidget(LaqeetApp(session: session, theme: ThemeController()));
    await session.start();
    await tester.pumpAndSettle();
    return (server, session);
  }

  Future<void> tapText(WidgetTester tester, String text) async {
    final f = find.text(text).last;
    await tester.ensureVisible(f);
    await tester.pumpAndSettle();
    await tester.tap(f);
    await tester.pumpAndSettle();
  }

  testWidgets('sign up → verify → profile → personalised jobs → save → account', (tester) async {
    final (server, session) = await boot(tester);
    expect(find.text('اعمل حساب مجاني'), findsOneWidget);

    await tapText(tester, 'اعمل حساب مجاني');
    // Empty submit shows every problem at once.
    await tapText(tester, 'اعمل الحساب');
    expect(find.text('اكتب الاسم'), findsOneWidget);
    expect(find.text('اختار الجنس'), findsOneWidget);
    expect(find.text('لازم توافق لتكمل'), findsOneWidget);
    expect(server.requests.where((r) => r.contains('register')), isEmpty);

    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), 'سارة');
    await tester.enterText(fields.at(1), 'الأحمد');
    await tester.enterText(fields.at(2), 'sara@example.com');
    await tester.enterText(fields.at(3), 'Yasmin-2026');
    await tester.enterText(fields.at(4), 'Yasmin-2026');
    await tapText(tester, 'أنثى');
    await tester.tap(find.byType(Checkbox));
    await tapText(tester, 'اعمل الحساب');

    expect(find.text('أكّد إيميلك'), findsOneWidget);
    expect(find.textContaining('sara@example.com', findRichText: true), findsOneWidget);
    await tester.enterText(find.byType(TextField), '111111');
    await tester.pumpAndSettle();
    expect(find.text('الكود غلط'), findsOneWidget);
    await tester.enterText(find.byType(TextField), '123456');
    await tester.pumpAndSettle();

    expect(find.text('أهلين سارة!'), findsOneWidget);
    await tapText(tester, 'فرجيني الوظائف');
    expect(find.text('اختار شهادتك'), findsOneWidget);
    await tapText(tester, 'بكالوريا');
    await tapText(tester, 'دمشق');
    await tapText(tester, 'فرجيني الوظائف');

    expect(session.status, SessionStatus.ready);
    expect(find.text('مناسبة إلك'), findsOneWidget);
    expect(find.textContaining('على قد شهادتك بمدنك'), findsOneWidget);
    expect(find.text('محاسب 1'), findsOneWidget);
    expect(server.requests.any((r) => r == 'GET /api/jobs'), isTrue);

    // Save from the list, then see it in محفوظاتي.
    await tester.tap(find.byTooltip('احفظ').first);
    await tester.pumpAndSettle();
    expect(server.saved, {1: 'saved'});
    await tapText(tester, 'محفوظاتي');
    expect(find.text('محاسب 1'), findsOneWidget);
    expect(find.text('محفوظة'), findsWidgets);

    // Details: apply buttons with the ready WhatsApp message.
    await tapText(tester, 'محاسب 1');
    expect(find.text('قدّم عالواتساب (الرسالة جاهزة)'), findsOneWidget);
    expect(find.textContaining('على قد شهادتك'), findsWidgets);
    expect(find.text('وين وصلت بهالوظيفة؟'), findsOneWidget);
    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();

    await tapText(tester, 'حسابي');
    expect(find.text('سارة الأحمد'), findsOneWidget);
    await tapText(tester, 'تسجيل خروج');
    await tapText(tester, 'اطلع');
    expect(session.loggedIn, isFalse);
    expect(find.text('اعمل حساب مجاني'), findsOneWidget);
  });

  testWidgets('guest can browse, and is asked to sign up to save', (tester) async {
    final (server, _) = await boot(tester);
    await tapText(tester, 'تصفّح الوظائف بلا حساب');
    expect(find.text('محاسب 1'), findsOneWidget);
    expect(find.text('مناسبة إلك'), findsNothing);
    // Plain-language search shows what it understood, and a chip can be removed.
    await tester.enterText(find.byType(TextField).first, 'محاسب بدمشق دوام جزئي');
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();
    expect(find.text('دمشق'), findsOneWidget);
    expect(find.text('دوام جزئي'), findsOneWidget);
    await tester.tap(find.byTooltip('شيل «دمشق»'));
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();
    expect(server.lastJobsQuery['q'], 'محاسب دوام جزئي');
    expect(find.text('دمشق'), findsNothing);

    await tester.tap(find.byTooltip('احفظ').first);
    await tester.pumpAndSettle();
    expect(find.text('عندي حساب'), findsOneWidget);
    expect(server.saved, isEmpty);
    await tapText(tester, 'عندي حساب');
    expect(find.text('أهلين فيك من جديد 👋'), findsOneWidget);
  });

  testWidgets('a shared job link opened at startup lands on that job, not lost to the splash screen', (tester) async {
    tester.binding.platformDispatcher.defaultRouteNameTestValue = '/j/7';
    addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
    final (server, _) = await boot(tester);
    expect(find.text('محاسب 7'), findsOneWidget);
    expect(find.text('قدّم عالواتساب (الرسالة جاهزة)'), findsOneWidget);
    expect(server.requests, contains('GET /api/jobs/7'));
  });

  testWidgets('wrong password shows the server message', (tester) async {
    final (server, _) = await boot(tester);
    server.users['a@b.co'] = {
      'password': 'Right-pass1',
      'public': {'id': 1, 'email': 'a@b.co', 'first_name': 'a', 'last_name': 'b', 'gender': 'male', 'email_verified': true, 'profile': {'education': 'bachelor'}},
    };
    await tapText(tester, 'عندي حساب، سجّل دخول');
    await tester.enterText(find.byType(TextFormField).at(0), 'a@b.co');
    await tester.enterText(find.byType(TextFormField).at(1), 'nope-nope');
    await tapText(tester, 'دخول');
    expect(find.text('الإيميل أو كلمة السر غلط'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).at(1), 'Right-pass1');
    await tapText(tester, 'دخول');
    expect(find.text('مناسبة إلك'), findsOneWidget);
  });
}
