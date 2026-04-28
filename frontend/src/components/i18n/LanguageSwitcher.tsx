'use client';

import { Languages } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Locale } from '@/lib/i18n';
import { useI18n } from './I18nProvider';

const OPTIONS: Array<{ locale: Locale; label: string }> = [
  { locale: 'zh-CN', label: '中文' },
  { locale: 'en-US', label: 'English' },
];

export function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale } = useI18n();

  return (
    <div className="flex items-center gap-2 rounded-md border bg-background p-1" aria-label="Language">
      {!compact && <Languages className="ml-1 h-4 w-4 text-muted-foreground" />}
      {OPTIONS.map((option) => (
        <Button
          key={option.locale}
          type="button"
          size="sm"
          variant={locale === option.locale ? 'default' : 'ghost'}
          className="h-7 px-2 text-xs"
          onClick={() => setLocale(option.locale)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}
