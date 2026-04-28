import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import { Toaster } from '@/components/ui/sonner';
import { AppLayout } from '@/components/layout/AppLayout';
import { I18nProvider } from '@/components/i18n/I18nProvider';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Presale Copilot',
  description: 'Enterprise intelligent proposal generation system',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={inter.className}>
        <I18nProvider>
          <AppLayout>
            {children}
          </AppLayout>
          <Toaster />
        </I18nProvider>
      </body>
    </html>
  );
}
