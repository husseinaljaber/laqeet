import 'package:flutter/gestures.dart';
import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api_client.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _form = GlobalKey<FormState>();
  final _first = TextEditingController();
  final _last = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  String? _gender;
  bool _terms = false;
  bool _busy = false;
  bool _tried = false;
  String? _error;
  final Map<String, String> _fieldErrors = {};

  @override
  void dispose() {
    for (final c in [_first, _last, _email, _password, _confirm]) {
      c.dispose();
    }
    super.dispose();
  }

  String? _name(String? v, String what) {
    final s = (v ?? '').trim();
    if (s.isEmpty) return 'اكتب $what';
    if (s.length < 2) return '$what قصير كتير';
    if (s.length > 40) return '$what طويل كتير';
    if (RegExp(r'[0-9٠-٩@#$%^&*()_+=<>/\\|{}\[\]~]').hasMatch(s)) return '$what لازم يكون حروف بس';
    return null;
  }

  Future<void> _submit() async {
    setState(() {
      _tried = true;
      _error = null;
      _fieldErrors.clear();
    });
    final ok = _form.currentState!.validate();
    if (!ok || _gender == null || !_terms) return;
    setState(() => _busy = true);
    try {
      await context.read<Session>().register(
            firstName: _first.text.trim(),
            lastName: _last.text.trim(),
            gender: _gender!,
            email: _email.text.trim(),
            password: _password.text,
            acceptedTerms: _terms,
          );
    } on ApiException catch (e) {
      setState(() {
        if (e.field != null && const {'first_name', 'last_name', 'email', 'password'}.contains(e.field)) {
          _fieldErrors[e.field!] = e.message;
        } else {
          _error = e.message;
        }
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _open(String path) {
    final base = context.read<Session>().repo.api.webBase;
    launchUrl(Uri.parse('$base$path'), mode: LaunchMode.externalApplication);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return FormPage(
      title: 'حساب جديد',
      children: [
        Form(
          key: _form,
          autovalidateMode: _tried ? AutovalidateMode.onUserInteraction : AutovalidateMode.disabled,
          child: AutofillGroup(
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text('بدقيقة وحدة بيصير عندك حساب', style: t.textTheme.headlineSmall),
              const SizedBox(height: 6),
              Text('مجاني، وما منعطي معلوماتك لحدا.', style: TextStyle(color: t.colorScheme.onSurfaceVariant)),
              const SizedBox(height: 24),
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: TextFormField(
                    controller: _first,
                    textCapitalization: TextCapitalization.words,
                    autofillHints: const [AutofillHints.givenName],
                    validator: (v) => _name(v, 'الاسم'),
                    decoration: InputDecoration(labelText: 'الاسم', errorText: _fieldErrors['first_name'], errorMaxLines: 2),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextFormField(
                    controller: _last,
                    textCapitalization: TextCapitalization.words,
                    autofillHints: const [AutofillHints.familyName],
                    validator: (v) => _name(v, 'الكنية'),
                    decoration: InputDecoration(labelText: 'الكنية', errorText: _fieldErrors['last_name'], errorMaxLines: 2),
                  ),
                ),
              ]),
              const SizedBox(height: 16),
              Text('الجنس', style: t.textTheme.titleSmall),
              const SizedBox(height: 8),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'male', label: Text('ذكر'), icon: Icon(Icons.male)),
                  ButtonSegment(value: 'female', label: Text('أنثى'), icon: Icon(Icons.female)),
                ],
                emptySelectionAllowed: true,
                selected: {?_gender},
                onSelectionChanged: (v) => setState(() => _gender = v.isEmpty ? null : v.first),
              ),
              if (_tried && _gender == null)
                Padding(
                  padding: const EdgeInsets.only(top: 6, right: 12),
                  child: Text('اختار الجنس', style: TextStyle(color: t.colorScheme.error, fontSize: 12)),
                ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _email,
                keyboardType: TextInputType.emailAddress,
                textDirection: TextDirection.ltr,
                autocorrect: false,
                autofillHints: const [AutofillHints.email],
                validator: validateEmail,
                decoration: InputDecoration(
                  labelText: 'الإيميل',
                  prefixIcon: const Icon(Icons.alternate_email),
                  helperText: 'رح نبعتلك عليه كود تأكيد',
                  errorText: _fieldErrors['email'],
                  errorMaxLines: 3,
                ),
              ),
              const SizedBox(height: 14),
              PasswordField(
                controller: _password,
                newPassword: true,
                validator: validateNewPassword,
                errorText: _fieldErrors['password'],
              ),
              const SizedBox(height: 14),
              PasswordField(
                controller: _confirm,
                label: 'أعد كتابة كلمة السر',
                validator: (v) => v != _password.text ? 'كلمتين السر مو متطابقين' : null,
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: 12),
              CheckboxListTile(
                value: _terms,
                onChanged: (v) => setState(() => _terms = v ?? false),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                title: Text.rich(TextSpan(style: t.textTheme.bodyMedium, children: [
                  const TextSpan(text: 'قرأت ووافقت على '),
                  TextSpan(
                    text: 'شروط الاستخدام',
                    style: TextStyle(color: t.colorScheme.primary, decoration: TextDecoration.underline),
                    recognizer: TapGestureRecognizer()..onTap = () => _open('/terms.html'),
                  ),
                  const TextSpan(text: ' و'),
                  TextSpan(
                    text: 'سياسة الخصوصية',
                    style: TextStyle(color: t.colorScheme.primary, decoration: TextDecoration.underline),
                    recognizer: TapGestureRecognizer()..onTap = () => _open('/privacy.html'),
                  ),
                ])),
                subtitle: _tried && !_terms
                    ? Text('لازم توافق لتكمل', style: TextStyle(color: t.colorScheme.error, fontSize: 12))
                    : null,
              ),
              const SizedBox(height: 8),
              if (_error != null) ...[ErrorBox(_error!), const SizedBox(height: 12)],
              BusyButton(label: 'اعمل الحساب', busy: _busy, onPressed: _submit),
              const SizedBox(height: 16),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                const Text('عندك حساب؟'),
                TextButton(onPressed: () => context.pushReplacement('/login'), child: const Text('سجّل دخول')),
              ]),
            ]),
          ),
        ),
      ],
    );
  }
}
