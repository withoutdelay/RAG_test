'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  Locale,
  normalizeLocale,
  translateText,
} from '@/lib/i18n';

type I18nContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (value: string) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

const TRANSLATABLE_ATTRIBUTES = ['aria-label', 'placeholder', 'title'] as const;
const SKIP_SELECTOR = [
  '[data-i18n-skip]',
  'textarea',
  'input',
  'pre',
  'code',
  'kbd',
  'samp',
  'script',
  'style',
  '[contenteditable="true"]',
  '.prose',
].join(',');

function shouldSkipElement(element: Element | null): boolean {
  return Boolean(element?.closest(SKIP_SELECTOR));
}

function walkTextNodes(root: ParentNode, callback: (node: Text) => void) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    callback(current as Text);
    current = walker.nextNode();
  }
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window === 'undefined') {
      return DEFAULT_LOCALE;
    }
    return normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY));
  });
  const originalTextRef = useRef(new WeakMap<Text, string>());

  const setLocale = useCallback((nextLocale: Locale) => {
    const normalized = normalizeLocale(nextLocale);
    window.localStorage.setItem(LOCALE_STORAGE_KEY, normalized);
    setLocaleState(normalized);
  }, []);

  const t = useCallback((value: string) => translateText(value, locale), [locale]);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dataset.locale = locale;

    const translateTextNode = (node: Text, allowSourceRefresh = false) => {
      if (shouldSkipElement(node.parentElement)) {
        return;
      }
      const originals = originalTextRef.current;
      if (!originals.has(node)) {
        originals.set(node, node.nodeValue ?? '');
      } else if (allowSourceRefresh) {
        const original = originals.get(node) ?? '';
        const expected = translateText(original, locale);
        const current = node.nodeValue ?? '';
        if (current !== expected) {
          originals.set(node, current);
        }
      }
      const original = originals.get(node) ?? '';
      const translated = translateText(original, locale);
      if (node.nodeValue !== translated) {
        node.nodeValue = translated;
      }
    };

    const translateAttributes = (root: ParentNode) => {
      if (!(root instanceof Element) && root !== document.body) {
        return;
      }
      const elements: Element[] = [];
      if (root instanceof Element) {
        elements.push(root);
      }
      elements.push(...Array.from((root as Element | Document['body']).querySelectorAll?.('*') ?? []));

      for (const element of elements) {
        if (shouldSkipElement(element)) {
          continue;
        }
        for (const attr of TRANSLATABLE_ATTRIBUTES) {
          const originalKey = `data-i18n-original-${attr}`;
          const current = element.getAttribute(attr);
          if (!current) {
            continue;
          }
          if (!element.hasAttribute(originalKey)) {
            element.setAttribute(originalKey, current);
          }
          const original = element.getAttribute(originalKey) ?? current;
          const translated = translateText(original, locale);
          if (current !== translated) {
            element.setAttribute(attr, translated);
          }
        }
      }
    };

    const applyTranslations = (root: ParentNode = document.body) => {
      walkTextNodes(root, translateTextNode);
      translateAttributes(root);
    };

    applyTranslations();
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === 'characterData') {
          translateTextNode(mutation.target as Text, true);
          continue;
        }
        for (const node of Array.from(mutation.addedNodes)) {
          if (node.nodeType === Node.TEXT_NODE) {
            translateTextNode(node as Text);
          } else if (node.nodeType === Node.ELEMENT_NODE) {
            applyTranslations(node as Element);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
  }, [locale]);

  const value = useMemo<I18nContextValue>(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used inside I18nProvider');
  }
  return context;
}
