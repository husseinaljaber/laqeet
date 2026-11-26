import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../state/session.dart';
import '../../widgets/common.dart';

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(24, 24, 24, 20),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Spacer(),
                const Align(alignment: AlignmentDirectional.centerStart, child: Logo(size: 48)),
                const SizedBox(height: 28),
                Text('شغل على قد شهادتك،\nبسوريا وتركيا', style: t.textTheme.headlineMedium),
                const SizedBox(height: 14),
                Text(
                  'منجمّع إعلانات الشغل من عشرات القنوات والمواقع كل يوم، ومنفرزها حسب شهادتك ومدينتك. مجاني للكل.',
                  style: t.textTheme.bodyLarge?.copyWith(color: t.colorScheme.onSurfaceVariant, height: 1.6),
                ),
                const SizedBox(height: 24),
                const _Point(Icons.school_outlined, 'بتشوف بس الوظائف اللي بتناسب شهادتك'),
                const _Point(Icons.chat_outlined, 'بتقدّم بكبسة: واتساب، اتصال، إيميل أو رابط'),
                const _Point(Icons.bookmark_border, 'بتحفظ الوظائف وبتتابع وين وصلت بكل وحدة'),
                const Spacer(flex: 2),
                FilledButton(onPressed: () => context.push('/register'), child: const Text('اعمل حساب مجاني')),
                const SizedBox(height: 10),
                OutlinedButton(onPressed: () => context.push('/login'), child: const Text('عندي حساب، سجّل دخول')),
                const SizedBox(height: 6),
                TextButton(
                  onPressed: () => context.read<Session>().continueAsGuest(),
                  child: const Text('تصفّح الوظائف بلا حساب'),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _Point extends StatelessWidget {
  const _Point(this.icon, this.text);

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Row(children: [
          Icon(icon, size: 22, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: Theme.of(context).textTheme.bodyLarge)),
        ]),
      );
}
