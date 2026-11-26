import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

class EditNameScreen extends StatefulWidget {
  const EditNameScreen({super.key});

  @override
  State<EditNameScreen> createState() => _EditNameScreenState();
}

class _EditNameScreenState extends State<EditNameScreen> {
  final _form = GlobalKey<FormState>();
  late final _u = context.read<Session>().user!;
  late final _first = TextEditingController(text: _u.firstName);
  late final _last = TextEditingController(text: _u.lastName);
  late String _gender = _u.gender;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _first.dispose();
    _last.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().updateName(firstName: _first.text.trim(), lastName: _last.text.trim(), gender: _gender);
      if (!mounted) return;
      toast(context, 'انحفظ ✓');
      context.pop();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => FormPage(title: 'الاسم والجنس', children: [
        Form(
          key: _form,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextFormField(
              controller: _first,
              validator: (v) => (v ?? '').trim().length < 2 ? 'اكتب الاسم' : null,
              decoration: const InputDecoration(labelText: 'الاسم'),
            ),
            const SizedBox(height: 14),
            TextFormField(
              controller: _last,
              validator: (v) => (v ?? '').trim().length < 2 ? 'اكتب الكنية' : null,
              decoration: const InputDecoration(labelText: 'الكنية'),
            ),
            const SizedBox(height: 16),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'male', label: Text('ذكر'), icon: Icon(Icons.male)),
                ButtonSegment(value: 'female', label: Text('أنثى'), icon: Icon(Icons.female)),
              ],
              selected: {_gender},
              onSelectionChanged: (v) => setState(() => _gender = v.first),
            ),
            const SizedBox(height: 24),
            if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
            BusyButton(label: 'احفظ', busy: _busy, onPressed: _save),
          ]),
        ),
      ]);
}

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _form = GlobalKey<FormState>();
  final _current = TextEditingController();
  final _next = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().changePassword(_current.text, _next.text);
      if (!mounted) return;
      toast(context, 'تغيّرت كلمة السر ✓ وطلعنا كل الأجهزة التانية');
      context.pop();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => FormPage(title: 'غيّر كلمة السر', children: [
        Form(
          key: _form,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            PasswordField(controller: _current, label: 'كلمة السر الحالية', validator: (v) => (v ?? '').isEmpty ? 'اكتبها' : null),
            const SizedBox(height: 14),
            PasswordField(
              controller: _next,
              label: 'كلمة السر الجديدة',
              newPassword: true,
              validator: (v) => validateNewPassword(v) ?? (v == _current.text ? 'لازم تكون غير الحالية' : null),
            ),
            const SizedBox(height: 24),
            if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
            BusyButton(label: 'غيّر', busy: _busy, onPressed: _save),
          ]),
        ),
      ]);
}

/// Required by the app stores: delete the account and everything saved with it, from inside the app.
class DeleteAccountScreen extends StatefulWidget {
  const DeleteAccountScreen({super.key});

  @override
  State<DeleteAccountScreen> createState() => _DeleteAccountScreenState();
}

class _DeleteAccountScreenState extends State<DeleteAccountScreen> {
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _password.dispose();
    super.dispose();
  }

  Future<void> _delete() async {
    if (_password.text.isEmpty) {
      setState(() => _error = 'اكتب كلمة السر لنتأكد إنه إنت');
      return;
    }
    final ok = await confirm(context, 'متأكد؟', 'رح ينحذف حسابك وكل محفوظاتك نهائياً، وما فينا نرجعهم.', 'احذف', danger: true);
    if (!ok || !mounted) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().deleteAccount(_password.text);
      if (mounted) toast(context, 'انحذف حسابك. الله يوفقك 🌱');
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return FormPage(title: 'احذف حسابي', children: [
      Icon(Icons.warning_amber_rounded, size: 48, color: t.colorScheme.error),
      const SizedBox(height: 12),
      const Text(
        'لما تحذف حسابك منمسح نهائياً: اسمك، إيميلك، ملفك (الشهادة والمدن)، وكل الوظائف المحفوظة ومتابعة التقديم. '
        'ما بيضل عنا شي بيدل عليك.',
        style: TextStyle(height: 1.7),
      ),
      const SizedBox(height: 20),
      PasswordField(controller: _password, label: 'كلمة السر'),
      const SizedBox(height: 20),
      if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
      FilledButton(
        style: FilledButton.styleFrom(backgroundColor: t.colorScheme.error, foregroundColor: t.colorScheme.onError),
        onPressed: _busy ? null : _delete,
        child: _busy ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5)) : const Text('احذف حسابي نهائياً'),
      ),
    ]);
  }
}
