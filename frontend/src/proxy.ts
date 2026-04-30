import { NextResponse, type NextRequest } from 'next/server';

const AUTH_ENABLED = process.env.NEXT_PUBLIC_AUTH_ENABLED === 'true';
const COOKIE_NAME = process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME || 'presale_session';

export function proxy(request: NextRequest) {
  if (!AUTH_ENABLED) {
    return NextResponse.next();
  }

  const { pathname, search } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get(COOKIE_NAME)?.value);

  if (pathname === '/login') {
    if (hasSession) {
      return NextResponse.redirect(new URL('/projects', request.url));
    }
    return NextResponse.next();
  }

  if (!hasSession) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('next', `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};

