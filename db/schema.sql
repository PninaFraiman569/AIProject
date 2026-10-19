-- מבנה מסד הנתונים (PostgreSQL / Supabase).
-- הבקאנד מריץ את הקובץ הזה אוטומטית בכל הפעלה. כל הפקודות בטוחות להרצה חוזרת
-- (IF NOT EXISTS), כך שהן לא מוחקות ולא משנות נתונים קיימים.
-- אפשר גם להריץ אותו ידנית ב-Supabase: SQL Editor -> להדביק -> Run.

-- התחייבויות: שורה לכל התחייבות
CREATE TABLE IF NOT EXISTS commitments (
    id                 BIGSERIAL PRIMARY KEY,
    source_file        TEXT        NOT NULL DEFAULT '',
    first_name         TEXT        NOT NULL DEFAULT '',
    last_name          TEXT        NOT NULL DEFAULT '',
    id_number          TEXT        NOT NULL DEFAULT '',
    central_numbers    TEXT[]      NOT NULL DEFAULT '{}',   -- כמה מספרים מרכזיים: כל אחד בנפרד
    received_date      TEXT        NOT NULL DEFAULT '',     -- DD/MM/YYYY
    sending_entity     TEXT        NOT NULL DEFAULT '',
    commitment_numbers TEXT[]      NOT NULL DEFAULT '{}',   -- כמה מספרי התחייבות: כל אחד בנפרד
    needs_review       BOOLEAN     NOT NULL DEFAULT FALSE,
    review_reason      TEXT        NOT NULL DEFAULT '',
    uncertain_fields   TEXT[]      NOT NULL DEFAULT '{}',   -- שדות שמסומנים בכתום
    status             TEXT        NOT NULL DEFAULT 'ממתין',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS commitments_status_idx ON commitments (status);

-- משתמשות: הסיסמה נשמרת מוצפנת (hash), לעולם לא כטקסט גלוי
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- התחברויות פעילות: נמחקות ביציאה או אחרי זמן ללא פעילות
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT        PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- אבטחה ב-Supabase: Supabase חושף אוטומטית טבלאות דרך ה-API שלו (עם המפתח הציבורי).
-- המערכת לא משתמשת ב-API הזה, ולכן מפעילים RLS בלי אף הרשאה - כך אי אפשר לקרוא
-- את הטבלאות משם. הבקאנד מתחבר ישירות כבעלים של הטבלאות, ולכן לא מושפע.
ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;
ALTER TABLE users       ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions    ENABLE ROW LEVEL SECURITY;
