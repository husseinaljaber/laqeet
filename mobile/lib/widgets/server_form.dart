import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../core/env.dart';
import '../state/session.dart';
import 'common.dart';

/// «عنوان موقع لقيت»: shown on first launch of a build that doesn't fix the server, or after a failed connection.
class ServerForm extends StatefulWidget {
  const ServerForm({super.key, this.firstTime = true});

  final bool firstTime;

  @override
  State<ServerForm> createState() => _ServerFormState();
}

class _ServerFormState extends State<ServerForm> {
  late final _c = TextEditingController(text: context.read<Session>().repo.api.webBase);
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final address = normalizeServer(_c.text);
    if (address == null) {
      setState(() => _error = 'العنوان مو صحيح. لازم يبلّش بـ https://');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<Session>().useServer(address);
    } catch (_) {
      if (mounted) setState(() => _error = 'ما في موقع لقيت شغّال على هالعنوان. تأكّد منه ومن النت.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(widget.firstTime ? 'وين موقع لقيت؟' : 'عنوان الموقع', style: t.textTheme.titleLarge, textAlign: TextAlign.center),
      const SizedBox(height: 8),
      Text('اكتب عنوان الموقع يلي بيشتغل عليه لقيت، متل laqeet.onrender.com',
          textAlign: TextAlign.center, style: TextStyle(color: t.colorScheme.onSurfaceVariant)),
      const SizedBox(height: 16),
      TextField(
        controller: _c,
        keyboardType: TextInputType.url,
        textDirection: TextDirection.ltr,
        autocorrect: false,
        onSubmitted: (_) => _save(),
        decoration: InputDecoration(labelText: 'عنوان الموقع', hintText: 'https://…', prefixIcon: const Icon(Icons.language), errorText: _error, errorMaxLines: 2),
      ),
      const SizedBox(height: 14),
      BusyButton(label: 'اتصل', busy: _busy, onPressed: _save),
    ]);
  }
}
