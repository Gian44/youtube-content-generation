import { NextResponse } from 'next/server';
import { getDashboardData, parseDashboardSlices } from '@/lib/dashboard-data';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const slices = parseDashboardSlices(searchParams.get('slices'));
    const force = ['1', 'true', 'yes'].includes((searchParams.get('force') || '').toLowerCase());
    const data = await getDashboardData({ slices, force });

    return NextResponse.json({
      success: true,
      ...data
    });
  } catch (error: any) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}
