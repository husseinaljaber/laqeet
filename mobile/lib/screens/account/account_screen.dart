import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/env.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

class AccountScreen extends StatelessWidget {
  const AccountScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final theme = context.watch<ThemeController>();
    final t = Theme.of(context);
    final u = s.user;
    final cfg = s.config;
    void open(String path) => launchUrl(Uri.parse('${s.repo.api.webBase}$path'), mode: LaunchMode.externalApplication);
    return Scaffold(
      appBar: AppBar(title: const Text('حسابي')),
      body: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
        if (u == null)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Text('إنت عم تتصفح بلا حساب', style: t.textTheme.titleMedium),
                  const SizedBox(height: 6),
                  const Text('اعمل حساب مجاني لنفرزلك الوظائف حسب شهادتك وتحفظ وتتابع تقديماتك.'),
                  const SizedBox(height: 14),
                  FilledButton(onPressed: () => context.push('/register'), child: const Text('اعمل حساب')),
                  const SizedBox(height: 8),
                  OutlinedButton(onPressed: () => context.push('/login'), child: const Text('سجّل دخول')),
                ]),
              ),
            ),
          )
        else ...[
          ListTile(
            contentPadding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
            leading: CircleAvatar(
              radius: 26,
              backgroundColor: t.colorScheme.primary,
              child: Text(u.firstName.isEmpty ? '؟' : u.firstName.characters.first,
                  style: TextStyle(color: t.colorScheme.onPrimary, fontSize: 22, fontWeight: FontWeight.w700)),
            ),
            title: Text(u.fullName, style: t.textTheme.titleMedium),
            subtitle: Text(u.email, textDirection: TextDirection.ltr, textAlign: TextAlign.end),
          ),
          const _Header('ملفي'),
          ListTile(
            leading: const Icon(Icons.school_outlined),
            title: const Text('شهادتي ومجالي ومدني'),
            subtitle: Text([
              u.profile.educations.isEmpty ? 'كل الشهادات' : u.profile.educations.map((e) => cfg?.educationLabel(e) ?? e).join('، '),
              if (u.profile.fields.isNotEmpty) u.profile.fields.map((f) => cfg?.fieldLabel(f) ?? f).join('، '),
              u.profile.cities.isEmpty ? 'كل المدن' : u.profile.cities.join('، '),
            ].whereType<String>().join(' · '), maxLines: 2, overflow: TextOverflow.ellipsis),
            trailing: const Icon(Icons.chevron_left),
            onTap: () => context.push('/account/profile'),
          ),
          ListTile(
            leading: const Icon(Icons.notifications_outlined),
            title: const Text('التنبيهات'),
            subtitle: const Text('خبّرني بالوظائف الجديدة'),
            trailing: const Icon(Icons.chevron_left),
            onTap: () => context.push('/account/alerts'),
          ),
          ListTile(
            leading: const Icon(Icons.badge_outlined),
            title: const Text('الاسم والجنس'),
            trailing: const Icon(Icons.chevron_left),
            onTap: () => context.push('/account/edit'),
          ),
          ListTile(
            leading: const Icon(Icons.lock_outline),
            title: const Text('غيّر كلمة السر'),
            trailing: const Icon(Icons.chevron_left),
            onTap: () => context.push('/account/password'),
          ),
        ],
        const _Header('المظهر'),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: SegmentedButton<ThemeMode>(
            segments: const [
              ButtonSegment(value: ThemeMode.system, label: Text('متل الجهاز'), icon: Icon(Icons.brightness_auto)),
              ButtonSegment(value: ThemeMode.light, label: Text('فاتح'), icon: Icon(Icons.light_mode_outlined)),
              ButtonSegment(value: ThemeMode.dark, label: Text('غامق'), icon: Icon(Icons.dark_mode_outlined)),
            ],
            selected: {theme.mode},
            onSelectionChanged: (v) => theme.set(v.first),
          ),
        ),
        const _Header('عن لقيت'),
        ListTile(leading: const Icon(Icons.privacy_tip_outlined), title: const Text('سياسة الخصوصية'), onTap: () => open('/privacy.html')),
        ListTile(leading: const Icon(Icons.description_outlined), title: const Text('شروط الاستخدام'), onTap: () => open('/terms.html')),
        ListTile(leading: const Icon(Icons.campaign_outlined), title: const Text('عندك شغل؟ انشر إعلان مجاناً'), onTap: () => open('/employer.html')),
        ListTile(leading: const Icon(Icons.gpp_maybe_outlined), title: const Text('افحص إعلان مشكوك فيه'), onTap: () => open('/scam.html')),
        if (u != null) ...[
          const Divider(height: 32),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('تسجيل خروج'),
            onTap: () async {
              final ok = await confirm(context, 'تسجيل خروج؟', 'فيك ترجع تدخل بأي وقت بإيميلك وكلمة السر.', 'اطلع');
              if (ok) await s.logout();
            },
          ),
          ListTile(
            leading: Icon(Icons.delete_forever_outlined, color: t.colorScheme.error),
            title: Text('احذف حسابي', style: TextStyle(color: t.colorScheme.error)),
            onTap: () => context.push('/account/delete'),
          ),
        ],
        const SizedBox(height: 16),
        Center(child: Text('لقيت $appVersion', style: t.textTheme.bodySmall)),
      ]),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 6),
        child: Text(text, style: Theme.of(context).textTheme.labelLarge?.copyWith(color: Theme.of(context).colorScheme.primary)),
      );
}
