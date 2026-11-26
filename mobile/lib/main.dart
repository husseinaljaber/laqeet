import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import 'core/api_client.dart';
import 'core/repository.dart';
import 'core/theme.dart';
import 'core/token_store.dart';
import 'router.dart';
import 'state/session.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final tokens = SecureTokenStore();
  final session = Session(repo: Repository(ApiClient(tokens: tokens)), tokens: tokens);
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

  @override
  void dispose() {
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
