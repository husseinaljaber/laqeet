import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../state/session.dart';
import '../widgets/common.dart';

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    return Scaffold(
      body: s.startupError != null
          ? MessageView(
              icon: Icons.wifi_off_rounded,
              title: 'ما قدرنا نوصل للسيرفر',
              body: s.startupError,
              action: 'جرّب كمان مرة',
              onAction: s.start,
            )
          : const Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
              Logo(size: 56),
              SizedBox(height: 28),
              SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.5)),
            ])),
    );
  }
}
