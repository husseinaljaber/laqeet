import 'dart:async';

import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import 'core/api_client.dart';
import 'core/push.dart';
import 'core/repository.dart';
import 'core/theme.dart';
import 'core/token_store.dart';
import 'router.dart';
import 'state/session.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final tokens = SecureTokenStore();
  final push = await createPush();
  final session = Session(repo: Repository(ApiClient(tokens: tokens)), tokens: tokens, push: push);
  final theme = ThemeController()..load();
  session.start();
  runApp(LaqeetApp(session: session, theme: theme));
}

class LaqeetApp extends StatefulWidget {
  const LaqeetApp({super.key, required this.session, required this.theme});

  final Session session;
  final ThemeController theme;

  @override
  State<LaqeetApp> createState() => _LaqeetAppState();
}

class _LaqeetAppState extends State<LaqeetApp> {
  late final GoRouter _router = buildRouter(widget.session);
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  final _subs = <StreamSubscription<Object?>>[];

  @override
  void initState() {
    super.initState();
    final push = widget.session.push;
    // A tapped notification opens its job (or the list); the router waits for start-up if needed.
    _subs.add(push.openedRoutes.listen(_router.go));
    push.initialRoute().then((r) {
      if (r != null) _router.go(r);
    });
    // While the app is open, Android doesn't show the notification itself: show it as a bar instead.
    _subs.add(push.foreground.listen((m) {
      final (title, body, route) = m;
      _messenger.currentState?.showSnackBar(SnackBar(
        content: Text(body.isEmpty ? title : '$title\n$body', maxLines: 3),
        action: SnackBarAction(label: 'شوف', onPressed: () => _router.go(route)),
      ));
    }));
  }

  @override
  void dispose() {
    for (final s in _subs) {
      s.cancel();
    }
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: widget.session),
          ChangeNotifierProvider.value(value: widget.theme),
        ],
        child: Consumer<ThemeController>(
          builder: (_, theme, _) => MaterialApp.router(
            title: 'لقيت',
            debugShowCheckedModeBanner: false,
            routerConfig: _router,
            scaffoldMessengerKey: _messenger,
            theme: buildTheme(Brightness.light),
            darkTheme: buildTheme(Brightness.dark),
            themeMode: theme.mode,
            locale: const Locale('ar'),
            supportedLocales: const [Locale('ar')],
            localizationsDelegates: GlobalMaterialLocalizations.delegates,
          ),
        ),
      );
}
