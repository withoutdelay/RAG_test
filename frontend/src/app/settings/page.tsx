'use client';

import { Languages } from 'lucide-react';
import { LanguageSwitcher } from '@/components/i18n/LanguageSwitcher';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/components/i18n/I18nProvider';

export default function SettingsPage() {
  const { locale } = useI18n();

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
        <p className="mt-2 text-muted-foreground">
          Manage workspace preferences for the current browser.
        </p>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Languages className="h-5 w-5 text-muted-foreground" />
            Interface Language
          </CardTitle>
          <CardDescription>
            Chinese is selected by default. English remains available for development and review.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium">Current language</p>
            <p className="text-sm text-muted-foreground">
              {locale === 'zh-CN' ? '中文交互' : 'English interface'}
            </p>
          </div>
          <LanguageSwitcher />
        </CardContent>
      </Card>
    </div>
  );
}
