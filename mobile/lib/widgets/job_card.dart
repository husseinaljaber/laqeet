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
  if (user == null || config == null || user.profile.education == null) return Fit.unknown;
  if (job.education == null) return Fit.match;
  return config.educationRank(job.education) <= config.educationRank(user.profile.education) ? Fit.match : Fit.above;
}

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
    final deadline = deadlineText(job.deadline);
    final muted = t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 8, 14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(job.title, style: t.textTheme.titleMedium, maxLines: 2, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text(
                    [job.employer ?? _sectorName(job.sector), if (job.cities.isNotEmpty) job.cities.take(2).join('، ')]
                        .join(' · '),
                    style: muted,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ]),
              ),
              IconButton(
                tooltip: saved ? 'شيل من المحفوظات' : 'احفظ',
                icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border, color: saved ? t.colorScheme.primary : null),
                onPressed: () => toggleSave(context, job.id),
              ),
            ]),
            const SizedBox(height: 10),
            Wrap(spacing: 6, runSpacing: 6, children: [
              if (job.official) const Tag('رسمية', icon: Icons.verified_outlined, color: AppColors.green),
              if (job.sector == 'ngo') const Tag('منظمة', icon: Icons.public),
              Tag(job.education == null ? 'شهادة غير مذكورة' : (s.config?.educationLabel(job.education) ?? ''), icon: Icons.school_outlined),
              if (job.experience != null && job.experience! > 0) Tag('خبرة ${job.experience}+ سنين', icon: Icons.timeline),
              if (job.hasWhatsApp) const Tag('واتساب', icon: Icons.chat_outlined, color: AppColors.whatsapp),
              if (deadline != null)
                Tag(deadline, icon: Icons.event_outlined, color: deadlineSoon(job.deadline) ? AppColors.warn : null),
            ]),
            const SizedBox(height: 8),
            Row(children: [
              if (fit != Fit.unknown) ...[
                Icon(fit == Fit.match ? Icons.check_circle : Icons.info_outline,
                    size: 16, color: fit == Fit.match ? AppColors.good : AppColors.warn),
                const SizedBox(width: 4),
                Text(fit == Fit.match ? 'على قد شهادتك' : 'بدها شهادة أعلى',
                    style: muted?.copyWith(color: fit == Fit.match ? AppColors.good : AppColors.warn)),
                const Spacer(),
              ] else
                const Spacer(),
              Text(ago(job.postedAt), style: muted),
              const SizedBox(width: 8),
            ]),
          ]),
        ),
      ),
    );
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
