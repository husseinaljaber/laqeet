import 'package:material_ui/material_ui.dart';

import '../core/api_client.dart';

/// Short message at the bottom of the screen.
void toast(BuildContext context, String message, {bool error = false}) {
  final m = ScaffoldMessenger.maybeOf(context);
  if (m == null) return;
  m
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(message),
      backgroundColor: error ? Theme.of(context).colorScheme.error : null,
    ));
}

String errorText(Object e) => e is ApiException ? e.message : 'صار خطأ، جرّب كمان مرة';

/// Centered icon + text + optional button, for empty lists and failures.
class MessageView extends StatelessWidget {
  const MessageView({super.key, required this.icon, required this.title, this.body, this.action, this.onAction});

  final IconData icon;
  final String title;
  final String? body;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(color: t.colorScheme.primary.withValues(alpha: 0.10), shape: BoxShape.circle),
            child: Icon(icon, size: 36, color: t.colorScheme.primary),
          ),
          const SizedBox(height: 16),
          Text(title, style: t.textTheme.titleMedium, textAlign: TextAlign.center),
          if (body != null) ...[
            const SizedBox(height: 6),
            Text(body!, style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant), textAlign: TextAlign.center),
          ],
          if (action != null) ...[
            const SizedBox(height: 20),
            SizedBox(width: 220, child: FilledButton(onPressed: onAction, child: Text(action!))),
          ],
        ]),
      ),
    );
  }
}

/// Password input with show/hide.
class PasswordField extends StatefulWidget {
  const PasswordField({
    super.key,
    required this.controller,
    this.label = 'كلمة السر',
    this.validator,
    this.errorText,
    this.newPassword = false,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String label;
  final String? Function(String?)? validator;
  final String? errorText;
  final bool newPassword;
  final ValueChanged<String>? onSubmitted;

  @override
  State<PasswordField> createState() => _PasswordFieldState();
}

class _PasswordFieldState extends State<PasswordField> {
  bool _hidden = true;

  @override
  Widget build(BuildContext context) => TextFormField(
        controller: widget.controller,
        obscureText: _hidden,
        autocorrect: false,
        enableSuggestions: false,
        textDirection: TextDirection.ltr,
        autofillHints: [widget.newPassword ? AutofillHints.newPassword : AutofillHints.password],
        validator: widget.validator,
        onFieldSubmitted: widget.onSubmitted,
        decoration: InputDecoration(
          labelText: widget.label,
          errorText: widget.errorText,
          errorMaxLines: 3,
          helperText: widget.newPassword ? '٨ حروف أو أكتر، ولا تكون سهلة متل 12345678' : null,
          prefixIcon: const Icon(Icons.lock_outline),
          suffixIcon: IconButton(
            tooltip: _hidden ? 'إظهار كلمة السر' : 'إخفاء كلمة السر',
            icon: Icon(_hidden ? Icons.visibility_outlined : Icons.visibility_off_outlined),
            onPressed: () => setState(() => _hidden = !_hidden),
          ),
        ),
      );
}

String? validateEmail(String? v) {
  final s = (v ?? '').trim();
  if (s.isEmpty) return 'اكتب إيميلك';
  if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$').hasMatch(s)) return 'الإيميل مو مكتوب صح';
  return null;
}

String? validateNewPassword(String? v) {
  final s = v ?? '';
  if (s.length < 8) return 'كلمة السر لازم تكون ٨ حروف أو أكتر';
  if (s.length > 128) return 'كلمة السر طويلة كتير';
  return null;
}

/// A button that shows a spinner while its async work runs, and blocks double taps.
class BusyButton extends StatelessWidget {
  const BusyButton({super.key, required this.label, required this.busy, required this.onPressed, this.icon});

  final String label;
  final bool busy;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => FilledButton(
        onPressed: busy ? null : onPressed,
        child: busy
            ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
            : Row(mainAxisSize: MainAxisSize.min, children: [
                if (icon != null) ...[Icon(icon, size: 20), const SizedBox(width: 8)],
                Text(label),
              ]),
      );
}

/// Narrow, centered column for forms (looks right on phones and on wide web windows).
class FormPage extends StatelessWidget {
  const FormPage({super.key, required this.children, this.title});

  final List<Widget> children;
  final String? title;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: title == null ? null : Text(title!)),
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 32), children: children),
            ),
          ),
        ),
      );
}

/// «لقيت» wordmark.
class Logo extends StatelessWidget {
  const Logo({super.key, this.size = 40});

  final double size;

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme.primary;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(
        width: size,
        height: size,
        decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(size * 0.3)),
        alignment: Alignment.center,
        child: Icon(Icons.work_outline, color: Theme.of(context).colorScheme.onPrimary, size: size * 0.55),
      ),
      SizedBox(width: size * 0.3),
      Text('لقيت',
          style: TextStyle(fontFamily: 'Alexandria', fontWeight: FontWeight.w800, fontSize: size * 0.75, color: c)),
    ]);
  }
}

/// Red box with the server's message (wrong password, too many tries…).
class ErrorBox extends StatelessWidget {
  const ErrorBox(this.message, {super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    final c = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: c.errorContainer, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          Icon(Icons.error_outline, color: c.onErrorContainer, size: 20),
          const SizedBox(width: 8),
          Expanded(child: Text(message, style: TextStyle(color: c.onErrorContainer))),
        ]),
      ),
    );
  }
}

Future<bool> confirm(BuildContext context, String title, String body, String yes, {bool danger = false}) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('لا')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: danger ? TextButton.styleFrom(foregroundColor: Theme.of(ctx).colorScheme.error) : null,
            child: Text(yes),
          ),
        ],
      ),
    ) ??
    false;
