import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../models/models.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import '../widgets/job_card.dart';

/// Saved jobs grouped by where the person is with each: saved, applied, interview…
class SavedScreen extends StatefulWidget {
  const SavedScreen({super.key});

  @override
  State<SavedScreen> createState() => _SavedScreenState();
}

class _SavedScreenState extends State<SavedScreen> {
  List<SavedItem>? _items;
  String? _error;
  String? _stage; // null = all
  Set<int> _lastIds = {};
  late final Session _session = context.read<Session>();

  @override
  void initState() {
    super.initState();
    _session.addListener(_onSession);
    _load();
  }

  @override
  void dispose() {
    _session.removeListener(_onSession);
    super.dispose();
  }

  /// Saved somewhere else in the app: reload when the set of saved ids changes.
  void _onSession() {
    if (!_session.loggedIn) return;
    if (_session.savedIds.length != _lastIds.length || !_session.savedIds.containsAll(_lastIds)) _load();
  }

  Future<void> _load() async {
    if (!_session.loggedIn) return;
    _lastIds = {..._session.savedIds};
    try {
      final items = await _session.repo.saved();
      if (!mounted) return;
      setState(() {
        _items = items;
        _error = null;
        _lastIds = items.map((i) => i.jobId).toSet();
      });
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    return Scaffold(
      appBar: AppBar(title: const Text('محفوظاتي')),
      body: !s.loggedIn
          ? MessageView(
              icon: Icons.bookmark_border,
              title: 'احفظ الوظائف وتابع تقديماتك',
              body: 'اعمل حساب مجاني لتحفظ الوظائف وتعرف وين وصلت بكل وحدة.',
              action: 'اعمل حساب',
              onAction: () => context.push('/register'),
            )
          : _content(context, s),
    );
  }

  Widget _content(BuildContext context, Session s) {
    final items = _items;
    if (items == null) {
      return _error != null
          ? MessageView(icon: Icons.wifi_off_rounded, title: 'ما قدرنا نجيب محفوظاتك', body: _error, action: 'جرّب كمان مرة', onAction: _load)
          : const Center(child: CircularProgressIndicator());
    }
    if (items.isEmpty) {
      return RefreshIndicator(
        onRefresh: _load,
        child: ListView(children: const [
          SizedBox(height: 80),
          MessageView(icon: Icons.bookmark_add_outlined, title: 'لسا ما حفظت شي', body: 'اكبس على 🔖 بأي وظيفة لتنحفظ هون.'),
        ]),
      );
    }
    final stages = s.config?.stages ?? const <Option>[];
    final counts = <String, int>{};
    for (final i in items) {
      counts[i.stage] = (counts[i.stage] ?? 0) + 1;
    }
    final shown = _stage == null ? items : items.where((i) => i.stage == _stage).toList();
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 24), children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(children: [
            ChoiceChip(label: Text('الكل (${items.length})'), selected: _stage == null, onSelected: (_) => setState(() => _stage = null)),
            for (final o in stages)
              if ((counts[o.code] ?? 0) > 0)
                Padding(
                  padding: const EdgeInsetsDirectional.only(start: 8),
                  child: ChoiceChip(
                    label: Text('${o.label} (${counts[o.code]})'),
                    selected: _stage == o.code,
                    onSelected: (_) => setState(() => _stage = o.code),
                  ),
                ),
          ]),
        ),
        const SizedBox(height: 12),
        for (final it in shown) ...[
          if (it.job != null)
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              _StageBadge(label: s.config?.stageLabel(it.stage) ?? it.stage, stage: it.stage),
              const SizedBox(height: 6),
              JobCard(job: it.job!, onTap: () => context.push('/job/${it.jobId}', extra: it.job)),
            ])
          else
            Card(
              child: ListTile(
                leading: const Icon(Icons.hide_source),
                title: const Text('هالإعلان انشال'),
                trailing: IconButton(
                  tooltip: 'شيله',
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () => toggleSave(context, it.jobId),
                ),
              ),
            ),
          const SizedBox(height: 10),
        ],
      ]),
    );
  }
}

class _StageBadge extends StatelessWidget {
  const _StageBadge({required this.label, required this.stage});

  final String label;
  final String stage;

  @override
  Widget build(BuildContext context) {
    final c = switch (stage) {
      'applied' => Colors.blue,
      'interview' => Colors.purple,
      'offered' => Colors.green,
      'rejected' => Colors.grey,
      _ => Theme.of(context).colorScheme.primary,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(999)),
      child: Text(label, style: TextStyle(color: c, fontWeight: FontWeight.w700, fontSize: 12)),
    );
  }
}
