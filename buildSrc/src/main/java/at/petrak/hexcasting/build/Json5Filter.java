package at.petrak.hexcasting.build;

import blue.endless.jankson.Jankson;
import blue.endless.jankson.JsonElement;
import blue.endless.jankson.JsonGrammar;
import blue.endless.jankson.JsonObject;
import blue.endless.jankson.api.SyntaxError;

import java.io.FilterReader;
import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.util.Map;

/**
 * Converts json5 resources to plain json, optionally flattening nested objects into dotted keys.
 * Re-implements the behaviour of the upstream PKPCPBP "PKJson5" Gradle plugin so the build does not
 * depend on that plugin.
 */
public final class Json5Filter {
    private static final Jankson JANKSON = Jankson.builder().build();

    private Json5Filter() {
    }

    public static final class Plain extends FilterReader {
        public Plain(Reader in) throws IOException {
            super(process(in, false));
        }
    }

    public static final class Flattening extends FilterReader {
        public Flattening(Reader in) throws IOException {
            super(process(in, true));
        }
    }

    static StringReader process(Reader in, boolean flatten) throws IOException {
        var sb = new StringBuilder();
        var buf = new char[8192];
        int n;
        while ((n = in.read(buf)) != -1) {
            sb.append(buf, 0, n);
        }
        // Normalise line endings and support backslash line continuations inside strings.
        var src = sb.toString().replace("\r\n", "\n").replaceAll("\\\\\\n\\s*", "\\\\\n");
        JsonObject obj;
        try {
            obj = JANKSON.load(src);
        } catch (SyntaxError e) {
            throw new IOException(e.getCompleteMessage(), e);
        }
        if (flatten) {
            var out = new JsonObject();
            flattenInto(null, obj, out);
            obj = out;
        }
        return new StringReader(obj.toJson(JsonGrammar.STRICT));
    }

    // Keys are joined with '.', except "" (refers to the parent key itself) and after a
    // prefix ending in one of ":_-/".
    private static void flattenInto(String prefix, JsonObject in, JsonObject out) {
        for (Map.Entry<String, JsonElement> e : in.entrySet()) {
            String key = e.getKey();
            String full;
            if (prefix == null || prefix.isEmpty()) {
                full = key;
            } else if (key.isEmpty()) {
                full = prefix;
            } else if (":_-/".indexOf(prefix.charAt(prefix.length() - 1)) != -1) {
                full = prefix + key;
            } else {
                full = prefix + "." + key;
            }
            if (e.getValue() instanceof JsonObject child) {
                flattenInto(full, child, out);
            } else {
                out.put(full, e.getValue());
            }
        }
    }
}
