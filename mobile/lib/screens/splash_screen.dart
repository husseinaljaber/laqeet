import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../state/session.dart';
import '../core/env.dart';
import '../widgets/common.dart';
import '../widgets/server_form.dart';

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    return Scaffold(
      body: s.serverMissing
          ? Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(24),
                child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 420), child: const ServerForm()),
              ),
            )
          : s.startupError != null
          ? Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Flexible(
                child: MessageView(
                  icon: Icons.wifi_off_rounded,
                  title: 'ما قدرنا نوصل للسيرفر',
                  body: s.startupError,
                  action: 'جرّب كمان مرة',
                  onAction: s.start,
                ),
              ),
              if (!serverFixed)
                TextButton(
                  onPressed: () => showModalBottomSheet<void>(
                    context: context,
                    isScrollControlled: true,
                    builder: (ctx) => Padding(
                      padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + MediaQuery.of(ctx).viewInsets.bottom),
                      child: const ServerForm(firstTime: false),
                    ),
                  ),
                  child: Text('غيّر عنوان الموقع (${s.repo.api.webBase})', textDirection: TextDirection.rtl),
                ),
              const SizedBox(height: 24),
            ])
          : const Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
              Logo(size: 56),
              SizedBox(height: 28),
              SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.5)),
            ])),
    );
  }
}
