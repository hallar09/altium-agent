object StatusForm: TStatusForm
  Left = 200
  Top = 200
  BorderIcons = [biSystemMenu, biMinimize]
  BorderStyle = bsSizeable
  Caption = 'Altium Agent'
  ClientHeight = 680
  ClientWidth = 400
  Color = $001C1612
  Constraints.MinHeight = 520
  Constraints.MinWidth = 400
  Font.Charset = DEFAULT_CHARSET
  Font.Color = $00F3EDE6
  Font.Height = -11
  Font.Name = 'Segoe UI'
  Font.Style = []
  FormStyle = fsStayOnTop
  OldCreateOrder = False
  ParentFont = False
  Position = poDesigned
  PixelsPerInch = 96
  TextHeight = 13
  OnClose = StatusFormClose
  object pnl_TopAccent: TPanel
    Left = 0
    Top = 0
    Width = 400
    Height = 3
    Align = alTop
    BevelOuter = bvNone
    Color = $003C8AE0
  
  end
  object pnl_Header: TPanel
    Left = 0
    Top = 3
    Width = 400
    Height = 94
    Align = alTop
    BevelOuter = bvNone
    Color = $001C1612
    object lbl_Brand: TLabel
      Left = 16
      Top = 12
      Width = 200
      Height = 18
      AutoSize = False
      Caption = 'ALTIUM AGENT'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $003C8AE0
      Font.Height = -13
      Font.Name = 'Segoe UI Semibold'
      Font.Style = [fsBold]
      ParentFont = False
    end
    object lbl_Tagline: TLabel
      Left = 16
      Top = 30
      Width = 260
      Height = 14
      AutoSize = False
      Caption = 'Altium Designer bridge for Claude'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $0073675C
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Version: TLabel
      Left = 296
      Top = 14
      Width = 90
      Height = 14
      AutoSize = False
      Alignment = taRightJustify
      Caption = 'v?'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $0073675C
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
    object pnl_StatusCard: TPanel
      Left = 12
      Top = 52
      Width = 376
      Height = 34
      BevelOuter = bvNone
      Color = $002B221B
      object pnl_StatusDot: TPanel
        Left = 12
        Top = 12
        Width = 10
        Height = 10
        BevelOuter = bvNone
        Caption = ''
        Color = $00A8C83C
      end
      object lbl_Spinner: TLabel
        Left = 28
        Top = 7
        Width = 16
        Height = 20
        AutoSize = False
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $003C8AE0
        Font.Height = -14
        Font.Name = 'Consolas'
        Font.Style = []
        ParentFont = False
      end
      object lbl_Status: TLabel
        Left = 46
        Top = 8
        Width = 320
        Height = 20
        AutoSize = False
        EllipsisPosition = epEndEllipsis
        Caption = 'ready'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00F3EDE6
        Font.Height = -13
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
  end
  object pnl_ErrBar: TPanel
    Left = 0
    Top = 97
    Width = 400
    Height = 20
    Align = alTop
    BevelOuter = bvNone
    Color = $001C1612
    object lbl_LastErr: TLabel
      Left = 16
      Top = 3
      Width = 370
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $005A60F0
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_KPIs: TPanel
    Left = 0
    Top = 117
    Width = 400
    Height = 82
    Align = alTop
    BevelOuter = bvNone
    Color = $001C1612
    object pnl_K1: TPanel
      Left = 12
      Top = 8
      Width = 88
      Height = 66
      BevelOuter = bvNone
      Color = $002B221B
      object pnl_K1Bar: TPanel
        Left = 0
        Top = 0
        Width = 88
        Height = 2
        BevelOuter = bvNone
        Caption = ''
        Color = $003B3026
      end
      object lbl_LblUp: TLabel
        Left = 0
        Top = 10
        Width = 88
        Height = 12
        AutoSize = False
        Alignment = taCenter
        Caption = 'UPTIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A5988B
        Font.Height = -9
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
      object lbl_ValUp: TLabel
        Left = 0
        Top = 26
        Width = 88
        Height = 30
        AutoSize = False
        Alignment = taCenter
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00F3EDE6
        Font.Height = -20
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K2: TPanel
      Left = 108
      Top = 8
      Width = 88
      Height = 66
      BevelOuter = bvNone
      Color = $002B221B
      object pnl_K2Bar: TPanel
        Left = 0
        Top = 0
        Width = 88
        Height = 2
        BevelOuter = bvNone
        Caption = ''
        Color = $003B3026
      end
      object lbl_LblReq: TLabel
        Left = 0
        Top = 10
        Width = 88
        Height = 12
        AutoSize = False
        Alignment = taCenter
        Caption = 'CALLS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A5988B
        Font.Height = -9
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
      object lbl_ValReq: TLabel
        Left = 0
        Top = 26
        Width = 88
        Height = 30
        AutoSize = False
        Alignment = taCenter
        Caption = '0'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00F3EDE6
        Font.Height = -20
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K3: TPanel
      Left = 204
      Top = 8
      Width = 88
      Height = 66
      BevelOuter = bvNone
      Color = $002B221B
      object pnl_K3Bar: TPanel
        Left = 0
        Top = 0
        Width = 88
        Height = 2
        BevelOuter = bvNone
        Caption = ''
        Color = $003B3026
      end
      object lbl_LblMs: TLabel
        Left = 0
        Top = 10
        Width = 88
        Height = 12
        AutoSize = False
        Alignment = taCenter
        Caption = 'IN ALTIUM'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A5988B
        Font.Height = -9
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
      object lbl_ValMs: TLabel
        Left = 0
        Top = 26
        Width = 88
        Height = 30
        AutoSize = False
        Alignment = taCenter
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00F3EDE6
        Font.Height = -20
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K4: TPanel
      Left = 300
      Top = 8
      Width = 88
      Height = 66
      BevelOuter = bvNone
      Color = $002B221B
      object pnl_K4Bar: TPanel
        Left = 0
        Top = 0
        Width = 88
        Height = 2
        BevelOuter = bvNone
        Caption = ''
        Color = $003B3026
      end
      object lbl_LblStop: TLabel
        Left = 0
        Top = 10
        Width = 88
        Height = 12
        AutoSize = False
        Alignment = taCenter
        Caption = 'AUTO-OFF'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A5988B
        Font.Height = -9
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
      object lbl_ValStop: TLabel
        Left = 0
        Top = 26
        Width = 88
        Height = 30
        AutoSize = False
        Alignment = taCenter
        Caption = '10m'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A8C83C
        Font.Height = -20
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
  end
  object pnl_Primary: TPanel
    Left = 0
    Top = 199
    Width = 400
    Height = 42
    Align = alTop
    BevelOuter = bvNone
    Color = $001C1612
    object btn_OpenWeb: TPanel
      Left = 12
      Top = 6
      Width = 376
      Height = 30
      BevelOuter = bvNone
      Caption = 'Open dashboard  (waiting for the server)'
      Color = $002B221B
      Cursor = crDefault
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $0073675C
      Font.Height = -12
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_OpenWebClick
      OnMouseEnter = btn_OpenWebEnter
      OnMouseLeave = btn_OpenWebLeave
    end
  end
  object pnl_Controls: TPanel
    Left = 0
    Top = 241
    Width = 400
    Height = 70
    Align = alTop
    BevelOuter = bvNone
    Color = $001C1612
    object btn_Pause: TPanel
      Left = 12
      Top = 4
      Width = 88
      Height = 26
      BevelOuter = bvNone
      Caption = 'Pause'
      Color = $00372C23
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F3EDE6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_PauseClick
      OnMouseEnter = btn_PauseEnter
      OnMouseLeave = btn_PauseLeave
    end
    object btn_Renew: TPanel
      Left = 108
      Top = 4
      Width = 88
      Height = 26
      BevelOuter = bvNone
      Caption = 'Keep alive'
      Color = $00372C23
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F3EDE6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = btn_RenewClick
      OnMouseEnter = btn_RenewEnter
      OnMouseLeave = btn_RenewLeave
    end
    object btn_ClearLog: TPanel
      Left = 204
      Top = 4
      Width = 88
      Height = 26
      BevelOuter = bvNone
      Caption = 'Clear'
      Color = $00372C23
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F3EDE6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_ClearLogClick
      OnMouseEnter = btn_ClearLogEnter
      OnMouseLeave = btn_ClearLogLeave
    end
    object btn_Detach: TPanel
      Left = 300
      Top = 4
      Width = 88
      Height = 26
      BevelOuter = bvNone
      Caption = 'Detach'
      Color = $002E2A5A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00B4B4FF
      Font.Height = -11
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 3
      OnClick = btn_DetachClick
      OnMouseEnter = btn_DetachEnter
      OnMouseLeave = btn_DetachLeave
    end
    object lbl_FilterLabel: TLabel
      Left = 14
      Top = 44
      Width = 34
      Height = 14
      AutoSize = False
      Caption = 'Filter'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A5988B
      Font.Height = -10
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
    end
    object edt_Filter: TEdit
      Left = 52
      Top = 41
      Width = 112
      Height = 20
      BorderStyle = bsNone
      Color = $002B221B
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F3EDE6
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
      TabOrder = 4
      Text = ''
      OnChange = edt_FilterChange
    end
    object chk_HidePings: TPanel
      Left = 172
      Top = 42
      Width = 74
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [x] pings'
      Color = $001C1612
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9C2BA
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 5
      OnClick = chk_HidePingsClick
      OnMouseEnter = chk_HidePingsEnter
      OnMouseLeave = chk_HidePingsLeave
    end
    object chk_OnlySlow: TPanel
      Left = 248
      Top = 42
      Width = 82
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [ ] >100ms'
      Color = $001C1612
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9C2BA
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 6
      OnClick = chk_OnlySlowClick
      OnMouseEnter = chk_OnlySlowEnter
      OnMouseLeave = chk_OnlySlowLeave
    end
    object chk_OnTop: TPanel
      Left = 332
      Top = 42
      Width = 60
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [x] pin'
      Color = $001C1612
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9C2BA
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 7
      OnClick = chk_OnTopClick
      OnMouseEnter = chk_OnTopEnter
      OnMouseLeave = chk_OnTopLeave
    end
  end
  object pnl_TabBar: TPanel
    Left = 0
    Top = 311
    Width = 400
    Height = 28
    Align = alTop
    BevelOuter = bvNone
    Color = $00221A15
    object tab_Log: TPanel
      Left = 12
      Top = 0
      Width = 72
      Height = 28
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Activity'
      Color = $001C1612
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $003C8AE0
      Font.Height = -11
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = tab_LogClick
      OnMouseEnter = tab_LogEnter
      OnMouseLeave = tab_LogLeave
    end
    object tab_Perf: TPanel
      Left = 84
      Top = 0
      Width = 72
      Height = 28
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Timing'
      Color = $00221A15
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A5988B
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = tab_PerfClick
      OnMouseEnter = tab_PerfEnter
      OnMouseLeave = tab_PerfLeave
    end
    object lbl_LogHint: TLabel
      Left = 160
      Top = 8
      Width = 228
      Height = 14
      AutoSize = False
      Alignment = taRightJustify
      Caption = 'newest first'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $0073675C
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
  end
  object mmo_Log: TMemo
    Left = 0
    Top = 339
    Width = 400
    Height = 307
    Align = alClient
    BorderStyle = bsNone
    Color = $00140F0C
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00DCD6D0
    Font.Height = -11
    Font.Name = 'Consolas'
    Font.Style = []
    ParentFont = False
    ReadOnly = True
    ScrollBars = ssVertical
    TabOrder = 0
  end
  object mmo_Perf: TMemo
    Left = 0
    Top = 339
    Width = 400
    Height = 307
    Align = alClient
    BorderStyle = bsNone
    Color = $00140F0C
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00DCD6D0
    Font.Height = -11
    Font.Name = 'Consolas'
    Font.Style = []
    ParentFont = False
    ReadOnly = True
    ScrollBars = ssVertical
    TabOrder = 1
    Visible = False
  end
  object pnl_Footer: TPanel
    Left = 0
    Top = 646
    Width = 400
    Height = 34
    Align = alBottom
    BevelOuter = bvNone
    Color = $002B221B
    object lbl_FooterHint: TLabel
      Left = 14
      Top = 4
      Width = 280
      Height = 12
      AutoSize = False
      Caption = 'Dashboard without an AI client:'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $0073675C
      Font.Height = -9
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_LaunchCmd: TLabel
      Left = 14
      Top = 17
      Width = 290
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = 'python -m altium_agent.server dashboard --port 8766'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9C2BA
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
    object btn_CopyCmd: TPanel
      Left = 316
      Top = 7
      Width = 72
      Height = 22
      BevelOuter = bvNone
      Caption = 'Copy'
      Color = $00372C23
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F3EDE6
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_CopyCmdClick
    end
  end
  object tmr_Spinner: TTimer
    Enabled = False
    Interval = 120
    OnTimer = tmr_SpinnerTimer
    Left = 340
    Top = 8
  end
  object tmr_Poll: TTimer
    Enabled = False
    Interval = 10
    OnTimer = tmr_PollTimer
    Left = 308
    Top = 8
  end
end
