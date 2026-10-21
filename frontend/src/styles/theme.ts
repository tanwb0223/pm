/**
 * 设计系统：颜色、圆角、阴影、间距
 *
 * 风格：现代 SaaS（参考 Linear / Vercel / Stripe Dashboard）
 * 主色改为靛蓝紫，替代原沉闷的深藏青
 */

// ================================================================ 品牌色
export const brand = {
  /** 主色：靛蓝（现代、专业、不呆板） */
  primary: '#4f46e5',
  primaryHover: '#6366f1',
  primaryActive: '#4338ca',
  primaryLight: '#eef2ff',
  primaryLighter: '#f5f7ff',

  /** 辅助色：青蓝，用于图表第二序列 */
  cyan: '#06b6d4',
  /** 辅助色：紫，用于图表第三序列 */
  violet: '#8b5cf6',
}

// ================================================================ 语义色
export const semantic = {
  success: '#10b981',
  successLight: '#ecfdf5',
  warning: '#f59e0b',
  warningLight: '#fffbeb',
  error: '#ef4444',
  errorLight: '#fef2f2',
  info: '#3b82f6',
  infoLight: '#eff6ff',
}

// ================================================================ 中性色
export const neutral = {
  /** 正文 */
  text: '#1f2937',
  textSecondary: '#6b7280',
  textTertiary: '#9ca3af',
  // 禁用态文字：原 #d1d5db 在白色背景上对比度仅约 1.4:1，
  // 按钮类禁用控件（如补填弹窗里锁定的报表类型）几乎看不清。
  // 提到 #9ca3af（对比度约 2.8:1），既明显弱于正文，又保持可读。
  textDisabled: '#9ca3af',

  /** 边框 */
  border: '#e5e7eb',
  borderLight: '#f3f4f6',

  /** 背景 */
  bgBody: '#f8fafc',
  bgCard: '#ffffff',
  bgHover: '#f9fafb',
  bgActive: '#f3f4f6',
}

// ================================================================ 图表配色
/** 按顺序取用，保证多序列图表协调 */
export const chartPalette = [
  '#4f46e5', // 靛蓝
  '#06b6d4', // 青
  '#8b5cf6', // 紫
  '#10b981', // 绿
  '#f59e0b', // 橙
  '#ef4444', // 红
  '#ec4899', // 粉
  '#14b8a6', // 蓝绿
]

/** 预警等级色 */
export const levelColors = {
  RED: '#ef4444',
  ORANGE: '#f59e0b',
  YELLOW: '#eab308',
}

/** 状态色 */
export const statusColors = {
  success: '#10b981',
  running: '#4f46e5',
  warning: '#f59e0b',
  danger: '#ef4444',
  default: '#9ca3af',
}

// ================================================================ 圆角与阴影
export const radius = {
  sm: 6,
  base: 8,
  lg: 12,
  xl: 16,
}

export const shadow = {
  /** 卡片默认 */
  card: '0 1px 3px 0 rgb(0 0 0 / 0.04), 0 1px 2px -1px rgb(0 0 0 / 0.03)',
  /** 卡片悬浮 */
  cardHover: '0 4px 12px -2px rgb(0 0 0 / 0.08), 0 2px 6px -2px rgb(0 0 0 / 0.04)',
  /** 弹层 */
  popup: '0 10px 30px -5px rgb(0 0 0 / 0.12), 0 4px 10px -4px rgb(0 0 0 / 0.06)',
  /** 登录卡片 */
  login: '0 20px 50px -12px rgb(0 0 0 / 0.25)',
}

// ================================================================ Ant Design 主题
export const antdTheme = {
  token: {
    colorPrimary: brand.primary,
    colorInfo: brand.primary,
    colorSuccess: semantic.success,
    colorWarning: semantic.warning,
    colorError: semantic.error,
    colorLink: brand.primary,

    colorText: neutral.text,
    colorTextSecondary: neutral.textSecondary,
    colorTextTertiary: neutral.textTertiary,
    colorTextDisabled: neutral.textDisabled,

    colorBorder: neutral.border,
    colorBorderSecondary: neutral.borderLight,

    colorBgLayout: neutral.bgBody,
    colorBgContainer: neutral.bgCard,
    // 按钮型 Radio / Select 等禁用态背景：用浅灰而非透明，
    // 与深一档的禁用文字搭配，既显示"不可编辑"又不糊成一片
    colorBgContainerDisabled: '#f3f4f6',

    borderRadius: radius.base,
    borderRadiusLG: radius.lg,
    borderRadiusSM: radius.sm,

    fontSize: 14,
    fontFamily:
      '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',

    /** 更柔和的阴影 */
    boxShadow: shadow.card,
    boxShadowSecondary: shadow.popup,

    /** 控件高度略增，更舒展 */
    controlHeight: 36,
    controlHeightLG: 42,
    controlHeightSM: 28,

    lineHeight: 1.6,
    wireframe: false,
  },

  components: {
    Layout: {
      headerBg: '#ffffff',
      headerHeight: 60,
      headerPadding: '0 24px',
      siderBg: '#ffffff',
      bodyBg: neutral.bgBody,
    },

    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: brand.primaryLight,
      itemSelectedColor: brand.primary,
      itemHoverBg: neutral.bgHover,
      itemColor: neutral.textSecondary,
      itemHoverColor: neutral.text,
      itemHeight: 40,
      itemMarginInline: 8,
      itemMarginBlock: 2,
      itemBorderRadius: radius.base,
      subMenuItemBg: 'transparent',
      iconSize: 16,
      collapsedIconSize: 18,
    },

    Table: {
      headerBg: '#fafbfc',
      headerColor: neutral.textSecondary,
      headerSplitColor: 'transparent',
      rowHoverBg: brand.primaryLighter,
      borderColor: neutral.borderLight,
      cellPaddingBlock: 12,
      headerBorderRadius: radius.base,
    },

    Card: {
      paddingLG: 20,
      headerFontSize: 15,
      headerHeight: 52,
    },

    Button: {
      fontWeight: 500,
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },

    Input: {
      activeShadow: `0 0 0 3px ${brand.primary}14`,
    },

    Select: {
      optionSelectedBg: brand.primaryLight,
    },

    Tag: {
      defaultBg: neutral.bgActive,
      defaultColor: neutral.textSecondary,
      borderRadiusSM: radius.sm,
    },

    Statistic: {
      contentFontSize: 28,
    },

    Tabs: {
      itemSelectedColor: brand.primary,
      inkBarColor: brand.primary,
      titleFontSize: 14,
    },

    Modal: {
      borderRadiusLG: radius.lg,
    },

    Drawer: {
      paddingLG: 24,
    },

    Segmented: {
      itemSelectedBg: '#ffffff',
      itemSelectedColor: brand.primary,
      trackBg: neutral.bgActive,
    },
  },
}
