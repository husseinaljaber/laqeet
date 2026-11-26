import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

/// Two steps: ask for a code by email, then type it with a new password (which also logs in).
class ForgotScreen extends StatefulWidget {
  const ForgotScreen({super.key, this.email});

  final String? email;

  @override
  State<ForgotScreen> createState() => _ForgotScreenState();
}

class _ForgotScreenState extends State<ForgotScreen> {
  final _form = GlobalKey<FormState>();
  late final _email = TextEditingController(text: widget.email);
  final _code = TextEditingController();
  final _password = TextEditingController();
  bool _sent = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _code.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() => _error = null);
    if (!_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await work();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _send() => _run(() async {
        await context.read<Session>().repo.forgotPassword(_email.text.trim());
        setState(() => _sent = true);
      });

  Future<void> _reset() => _run(() async {
        await context.read<Session>().resetPassword(_email.text.trim(), _code.text.trim(), _password.text);
        if (mounted) toast(context, 'تغيّرت كلمة السر ✓');
      });

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return FormPage(
      title: 'نسيت كلمة السر',
      children: [
        Form(
          key: _form,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(
              _sent
                  ? 'إذا الإيميل مسجّل عنا، وصلك كود من ٦ أرقام. اكتبه هون مع كلمة سر جديدة.'
                  : 'اكتب إيميلك ومنبعتلك كود لتعمل كلمة سر جديدة.',
              style: t.textTheme.bodyLarge?.copyWith(height: 1.6),
            ),
            const SizedBox(height: 20),
            TextFormField(
              controller: _email,
              enabled: !_sent,
              keyboardType: TextInputType.emailAddress,
              textDirection: TextDirection.ltr,
              autocorrect: false,
              validator: validateEmail,
              decoration: const InputDecoration(labelText: 'الإيميل', prefixIcon: Icon(Icons.alternate_email)),
            ),
            if (_sent) ...[
              const SizedBox(height: 14),
              TextFormField(
                controller: _code,
                keyboardType: TextInputType.number,
                textDirection: TextDirection.ltr,
                maxLength: 6,
                autofillHints: const [AutofillHints.oneTimeCode],
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                validator: (v) => (v ?? '').length != 6 ? 'الكود ٦ أرقام' : null,
                decoration: const InputDecoration(labelText: 'الكود', prefixIcon: Icon(Icons.pin_outlined), counterText: ''),
              ),
              const SizedBox(height: 14),
              PasswordField(controller: _password, label: 'كلمة السر الجديدة', newPassword: true, validator: validateNewPassword),
            ],
            const SizedBox(height: 20),
            if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
            BusyButton(
              label: _sent ? 'غيّر كلمة السر وادخل' : 'ابعتلي الكود',
              busy: _busy,
              onPressed: _sent ? _reset : _send,
            ),
            if (_sent)
              TextButton(
                onPressed: _busy ? null : () => setState(() => _sent = false),
                child: const Text('ما وصلني شي، ابعت كمان مرة'),
              ),
          ]),
        ),
      ],
    );
  }
}
