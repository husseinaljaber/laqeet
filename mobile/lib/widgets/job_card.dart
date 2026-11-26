import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../core/format.dart';
import '../core/theme.dart';
import '../models/models.dart';
import '../state/session.dart';
import 'common.dart';

/// How a job's required education compares with the person's own.
enum Fit { match, above, unknown }

Fit fitFor(Job job, User? user, AppConfig? config) {
  final mine = user?.profile.education; // the highest degree they picked
  if (user == null || config == null || mine == null) return Fit.unknown;
  if (job.education == null || user.profile.educations.contains(job.education)) return Fit.match;
  return config.educationRank(job.education) <= config.educationRank(mine) ? Fit.match : Fit.above;
}

/// One job in a list: big enough to decide from without opening it.
class JobCard extends StatelessWidget {
  const JobCard({super.key, required this.job, required this.onTap});

  final Job job;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final s = context.watch<Session>();
    final saved = s.isSaved(job.id);
    final fit = fitFor(job, s.user, s.config);
    final muted = t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant);
    final edu = job.education == null ? 'الشهادة مو مذكورة' : (s.config?.educationLabel(job.education) ?? '');
    final where = job.cities.isNotEmpty ? job.cities.take(3).join('، ') : (job.country == 'tr' ? 'تركيا' : 'سوريا');
    final summary = (job.summary ?? '').trim();
    final showSummary = summary.isNotEmpty && summary != job.title;
    final deadline = deadlineText(job.deadline);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 16, 10, 14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (job.official || job.sector != 'private')
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Wrap(spacing: 6, children: [
                        if (job.official) const Tag('رسمية', icon: Icons.verified_outlined, color: AppColors.green),
                        if (job.sector == 'gov' && !job.official) const Tag('حكومي', icon: Icons.account_balance_outlined),
                        if (job.sector == 'ngo') const Tag('منظمة', icon: Icons.public),
                      ]),
                    ),
                  Text(job.title, style: t.textTheme.titleLarge?.copyWith(height: 1.35), maxLines: 3, overflow: TextOverflow.ellipsis),
                ]),
              ),
              IconButton(
                tooltip: saved ? 'شيل من المحفوظات' : 'احفظ',
                iconSize: 28,
                icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border, color: saved ? t.colorScheme.primary : null),
                onPressed: () => toggleSave(context, job.id),
              ),
            ]),
            const SizedBox(height: 10),
            _Line(Icons.apartment_outlined, job.employer ?? _sectorName(job.sector), style: t.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600)),
            _Line(Icons.place_outlined, where, style: muted),
            _Line(Icons.school_outlined,
                [edu, if (job.experience != null && job.experience! > 0) 'خبرة ${job.experience}+ سنين' else if (job.experience == 0) 'بلا خبرة'].join(' · '),
                style: muted),
            if (job.salary != null && job.salary!.trim().isNotEmpty) _Line(Icons.payments_outlined, job.salary!, style: muted),
            if (job.shift != null && job.shift!.trim().isNotEmpty) _Line(Icons.schedule, job.shift!, style: muted),
            if (showSummary)
              Padding(
                padding: const EdgeInsets.only(top: 8, left: 8),
                child: Text(summary, maxLines: 2, overflow: TextOverflow.ellipsis, style: muted?.copyWith(height: 1.6)),
              ),
            const SizedBox(height: 12),
            Container(
              margin: const EdgeInsets.only(left: 8),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: t.colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                _DateRow(
                  icon: Icons.event_available_outlined,
                  label: 'نُشر',
                  value: [dateAr(job.postedAt), if (ago(job.postedAt).isNotEmpty) '(${ago(job.postedAt)})'].join(' '),
                ),
                const SizedBox(height: 6),
                _DateRow(
                  icon: Icons.event_busy_outlined,
                  label: 'آخر موعد',
                  value: job.deadline == null ? 'ما في موعد محدد' : '${dateAr(job.deadline)} · $deadline',
                  color: deadlineSoon(job.deadline) ? AppColors.warn : null,
                ),
              ]),
            ),
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.only(left: 8),
              child: Wrap(spacing: 6, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
                if (fit != Fit.unknown)
                  Tag(fit == Fit.match ? 'على قد شهادتك' : 'بدها شهادة أعلى',
                      icon: fit == Fit.match ? Icons.check_circle : Icons.info_outline,
                      color: fit == Fit.match ? AppColors.good : AppColors.warn),
                if (job.hasWhatsApp) const Tag('واتساب', icon: Icons.chat_outlined, color: AppColors.whatsapp),
                if (job.phones.isNotEmpty && !job.hasWhatsApp) const Tag('اتصال', icon: Icons.call_outlined),
                if (job.email != null) const Tag('إيميل', icon: Icons.email_outlined),
                if (job.applyUrl != null) const Tag('رابط تقديم', icon: Icons.link),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.icon, this.text, {this.style});

  final IconData icon;
  final String text;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 6, left: 8),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(icon, size: 20, color: Theme.of(context).colorScheme.primary),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: style, maxLines: 2, overflow: TextOverflow.ellipsis)),
        ]),
      );
}

class _DateRow extends StatelessWidget {
  const _DateRow({required this.icon, required this.label, required this.value, this.color});

  final IconData icon;
  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = color ?? t.colorScheme.onSurface;
    return Row(children: [
      Icon(icon, size: 18, color: color ?? t.colorScheme.onSurfaceVariant),
      const SizedBox(width: 8),
      Text('$label: ', style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
      Expanded(child: Text(value, style: t.textTheme.bodyMedium?.copyWith(color: c, fontWeight: FontWeight.w600))),
    ]);
  }
}

String _sectorName(String sector) => switch (sector) {
      'gov' => 'جهة حكومية',
      'ngo' => 'منظمة',
      _ => 'شركة خاصة',
    };

/// Save or unsave a job; asks guests to sign in first.
Future<void> toggleSave(BuildContext context, int jobId) async {
  final s = context.read<Session>();
  if (!s.loggedIn) {
    askToSignIn(context, 'لتحفظ الوظائف وترجعلها من أي جهاز، اعمل حساب مجاني.');
    return;
  }
  final wasSaved = s.isSaved(jobId);
  try {
    await s.toggleSaved(jobId);
    if (context.mounted) toast(context, wasSaved ? 'انشالت من المحفوظات' : 'انحفظت ✓');
  } catch (e) {
    if (context.mounted) toast(context, errorText(e), error: true);
  }
}

void askToSignIn(BuildContext context, String why) {
  showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (ctx) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('سجّل دخول', style: Theme.of(ctx).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(why),
          const SizedBox(height: 20),
          FilledButton(
              onPressed: () {
                Navigator.pop(ctx);
                context.push('/register');
              },
              child: const Text('اعمل حساب')),
          const SizedBox(height: 8),
          OutlinedButton(
              onPressed: () {
                Navigator.pop(ctx);
                context.push('/login');
              },
              child: const Text('عندي حساب')),
        ]),
      ),
    ),
  );
}

class Tag extends StatelessWidget {
  const Tag(this.text, {super.key, this.icon, this.color});

  final String text;
  final IconData? icon;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = color ?? t.colorScheme.onSurfaceVariant;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.09), borderRadius: BorderRadius.circular(999)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: 14, color: c), const SizedBox(width: 4)],
        Text(text, style: t.textTheme.labelMedium?.copyWith(color: c)),
      ]),
    );
  }
}
