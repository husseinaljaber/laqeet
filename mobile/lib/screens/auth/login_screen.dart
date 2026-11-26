import 'package:go_router/go_router.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../../state/session.dart';
import '../../widgets/common.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() => _error = null);
    if (!_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await context.read<Session>().login(_email.text.trim(), _password.text);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => FormPage(
        title: 'تسجيل الدخول',
        children: [
          Form(
            key: _form,
            child: AutofillGroup(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Text('أهلين فيك من جديد 👋', style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 24),
                TextFormField(
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  textDirection: TextDirection.ltr,
                  autocorrect: false,
                  autofillHints: const [AutofillHints.email],
                  validator: validateEmail,
                  decoration: const InputDecoration(labelText: 'الإيميل', prefixIcon: Icon(Icons.alternate_email)),
                ),
                const SizedBox(height: 14),
                PasswordField(
                  controller: _password,
                  validator: (v) => (v ?? '').isEmpty ? 'اكتب كلمة السر' : null,
                  onSubmitted: (_) => _submit(),
                ),
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: TextButton(
                    onPressed: () => context.push(Uri(path: '/forgot', queryParameters: {
                      if (_email.text.trim().isNotEmpty) 'email': _email.text.trim(),
                    }).toString()),
                    child: const Text('نسيت كلمة السر؟'),
                  ),
                ),
                if (_error != null) ...[
                  ErrorBox(_error!),
                  const SizedBox(height: 12),
                ],
                BusyButton(label: 'دخول', busy: _busy, onPressed: _submit),
                const SizedBox(height: 16),
                Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                  const Text('ما عندك حساب؟'),
                  TextButton(onPressed: () => context.pushReplacement('/register'), child: const Text('اعمل حساب')),
                ]),
              ]),
            ),
          ),
        ],
      );
}
