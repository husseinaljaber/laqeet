import 'dart:async';

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

/// The 6-digit code sent to the email at sign-up.
class VerifyScreen extends StatefulWidget {
  const VerifyScreen({super.key});

  @override
  State<VerifyScreen> createState() => _VerifyScreenState();
}

class _VerifyScreenState extends State<VerifyScreen> {
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;
  int _wait = 60;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _startTimer();
  }

  void _startTimer() {
    _timer?.cancel();
    setState(() => _wait = 60);
    _timer = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      setState(() => _wait--);
      if (_wait <= 0) t.cancel();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _code.text.trim();
    if (code.length != 6) {
      setState(() => _error = 'الكود ٦ أرقام');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().verify(code);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resend() async {
    try {
      await context.read<Session>().repo.resendVerification();
      if (!mounted) return;
      toast(context, 'بعتنالك كود جديد');
      _startTimer();
    } catch (e) {
      if (mounted) toast(context, errorText(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<Session>();
    final t = Theme.of(context);
    return FormPage(
      children: [
        const SizedBox(height: 12),
        Icon(Icons.mark_email_unread_outlined, size: 56, color: t.colorScheme.primary),
        const SizedBox(height: 16),
        Text('أكّد إيميلك', style: t.textTheme.headlineSmall, textAlign: TextAlign.center),
        const SizedBox(height: 8),
        Text.rich(
          TextSpan(children: [
            const TextSpan(text: 'بعتنا كود من ٦ أرقام على\n'),
            TextSpan(text: s.user?.email ?? '', style: const TextStyle(fontWeight: FontWeight.w700)),
            const TextSpan(text: '\nإذا ما لقيته، شوف بمجلد الرسائل المزعجة (Spam).'),
          ]),
          textAlign: TextAlign.center,
          style: TextStyle(color: t.colorScheme.onSurfaceVariant, height: 1.6),
        ),
        const SizedBox(height: 24),
        TextField(
          controller: _code,
          autofocus: true,
          keyboardType: TextInputType.number,
          textAlign: TextAlign.center,
          textDirection: TextDirection.ltr,
          maxLength: 6,
          autofillHints: const [AutofillHints.oneTimeCode],
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          style: const TextStyle(fontSize: 28, letterSpacing: 12, fontWeight: FontWeight.w700),
          decoration: const InputDecoration(hintText: '••••••', counterText: '', semanticCounterText: 'كود التأكيد'),
          onChanged: (v) {
            if (v.length == 6) _submit();
          },
        ),
        const SizedBox(height: 16),
        if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
        BusyButton(label: 'تأكيد', busy: _busy, onPressed: _submit),
        const SizedBox(height: 12),
        TextButton(
          onPressed: _wait > 0 ? null : _resend,
          child: Text(_wait > 0 ? 'إعادة إرسال الكود بعد $_wait ثانية' : 'ابعتلي كود جديد'),
        ),
        TextButton(
          onPressed: () => s.logout(),
          child: const Text('الإيميل غلط؟ ارجع وسجّل بإيميل تاني'),
        ),
      ],
    );
  }
}
