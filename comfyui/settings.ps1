param (
    [Parameter(HelpMessage=@"
获取 ComfyUI Installer 的帮助信息
"@)][switch]$Help,

    [Parameter(HelpMessage=@"
设置内核的路径前缀, 默认路径前缀为 core
"@)][string]$CorePrefix,

    [Parameter(HelpMessage=@"
禁用 ComfyUI Installer 自动设置代理服务器
"@)][switch]$DisableProxy,

    [Parameter(HelpMessage=@"
使用自定义的代理服务器地址, 例如代理服务器地址为 http://127.0.0.1:10809, 则使用 -UseCustomProxy `"http://127.0.0.1:10809`" 设置代理服务器地址
"@)][string]$UseCustomProxy,

    [Parameter(HelpMessage=@"
脚本执行完成后不暂停, 直接退出
"@)][switch]$NoPause
)
try {
    $config = @{
        OriginalScriptPath = $script:PSCommandPath
        LaunchCommandLine = if ($script:MyInvocation.Line) { $script:MyInvocation.Line } else { $([Environment]::CommandLine) }
        Help = $script:Help
        CorePrefix = $script:CorePrefix
        DisableProxy = $script:DisableProxy
        UseCustomProxy = $script:UseCustomProxy
        NoPause = $script:NoPause
    }
    (Import-Module (Join-Path $PSScriptRoot "modules.psm1") -Function "Join-NormalizedPath", "Get-TrimmedTextFile", "Resolve-CorePrefix", "Initialize-EnvPath", "Write-Log", "Format-CommandLineArgumentForLog", "Format-CoreCliCommandForLog", "Write-CoreCliFailureCommand", "Set-CorePrefix", "Get-Version", "Update-Installer", "Set-Proxy", "Write-FileWithStreamWriter", "Get-HelpMessage", "Test-PythonAndGit", "Get-NativeCommandExitCode", "Exit-ManagerScript" -PassThru -Force -ErrorAction Stop).Invoke({
        param ($cfg)
        $script:OriginalScriptPath = $cfg.OriginalScriptPath
        $script:LaunchCommandLine = $cfg.LaunchCommandLine
        $script:Help = $cfg.Help
        $script:CorePrefix = $cfg.CorePrefix
        $script:DisableProxy = $cfg.DisableProxy
        $script:UseCustomProxy = $cfg.UseCustomProxy
        $script:NoPause = $cfg.NoPause
    }, $config)
}
catch {
    Write-Error "导入 Installer 模块发生错误: $_"
    Write-Host "这可能是 Installer 文件出现了损坏, 请运行 " -ForegroundColor White -NoNewline
    Write-Host "launch_comfyui_installer.ps1" -ForegroundColor Yellow -NoNewline
    Write-Host " 脚本修复该问题" -ForegroundColor White
    if (!($script:NoPause)) { Read-Host | Out-Null }
    exit 1
}


# 通用开关状态获取
function Get-ToggleStatus ([string]$file, [string]$trueLabel = "启用", [string]$falseLabel = "禁用", [bool]$reverse = $false) {
    $exists = Test-Path (Join-NormalizedPath $PSScriptRoot $file)
    if ($reverse) {
        if ($exists) { return $falseLabel } else { return $trueLabel }
    }
    if ($exists) { return $trueLabel } else { return $falseLabel }
}


# 通用文本配置获取
function Get-TextStatus ([string]$file, [string]$defaultLabel = "无") {
    $value = Get-TrimmedTextFile (Join-NormalizedPath $PSScriptRoot $file) -Encoding UTF8
    if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    return $defaultLabel
}


# 通用开关切换逻辑
function Set-ToggleSetting ([string]$file, [string]$name, [bool]$enable) {
    # 如果文件名以 disable 开头, 则 enable=true 表示删除文件, enable=false 表示创建文件
    if ($file.ToLower().StartsWith("disable")) {
        if ($enable) {
            if (Test-Path (Join-NormalizedPath $PSScriptRoot $file)) { Remove-Item (Join-NormalizedPath $PSScriptRoot $file) -Force -ErrorAction SilentlyContinue }
        } else {
            if (!(Test-Path (Join-NormalizedPath $PSScriptRoot $file))) { New-Item -ItemType File -Path (Join-NormalizedPath $PSScriptRoot $file) -Force > $null }
        }
    } else {
        # 普通开关: enable=true 表示创建文件, enable=false 表示删除文件
        if ($enable) {
            if (!(Test-Path (Join-NormalizedPath $PSScriptRoot $file))) { New-Item -ItemType File -Path (Join-NormalizedPath $PSScriptRoot $file) -Force > $null }
        } else {
            if (Test-Path (Join-NormalizedPath $PSScriptRoot $file)) { Remove-Item (Join-NormalizedPath $PSScriptRoot $file) -Force -ErrorAction SilentlyContinue }
        }
    }
    Write-Log "$name 设置成功"
}


# 更新网络代理设置
function Update-ProxySetting {
    while ($true) {
        $proxy_status = Get-TextStatus "proxy.txt" $null
        $current = if (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt")) { "禁用" } elseif (-not [string]::IsNullOrWhiteSpace($proxy_status)) { "自定义: $proxy_status" } else { "系统代理" }
        Write-Log "当前网络代理设置: $current"
        Write-Log "1. 使用系统代理 | 2. 手动输入代理地址 | 3. 禁用代理 | 4. 返回上一级"
        $choice = Get-UserInput
        if ($choice -eq "1") { Remove-Item (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt"), (Join-NormalizedPath $PSScriptRoot "proxy.txt") -Force -ErrorAction SilentlyContinue; break }
        elseif ($choice -eq "2") {
            $addr = Read-SingleLineSettingEditor -Prompt "代理地址 (如 http://127.0.0.1:10809):" -InitialText $proxy_status
            if ($null -eq $addr) { Write-Log "已取消修改代理地址"; return }
            if ($addr) {
                Remove-Item (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt") -Force -ErrorAction SilentlyContinue
                Set-Content -Path (Join-NormalizedPath $PSScriptRoot "proxy.txt") -Value $addr -Encoding UTF8
            }
            break
        }
        elseif ($choice -eq "3") {
            New-Item (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt") -Force > $null
            Remove-Item (Join-NormalizedPath $PSScriptRoot "proxy.txt") -Force -ErrorAction SilentlyContinue
            break
        }
        elseif ($choice -eq "4") { return }
    }
}


# 更新镜像设置
function Update-Mirror-Setting ([string]$file, [string]$name, [string[]]$examples) {
    while ($true) {
        $mirror_status = Get-TextStatus $file $null
        $current = if (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_$file")) { "禁用" } elseif (-not [string]::IsNullOrWhiteSpace($mirror_status)) { "自定义: $mirror_status" } else { "默认" }
        Write-Log "当前 $name 设置: $current"
        Write-Log "1. 使用默认或自动镜像 | 2. 手动输入镜像地址 | 3. 禁用该镜像 | 4. 返回上一级"
        $choice = Get-UserInput
        if ($choice -eq "1") { Remove-Item (Join-NormalizedPath $PSScriptRoot "disable_$file"), (Join-NormalizedPath $PSScriptRoot $file) -Force -ErrorAction SilentlyContinue; break }
        elseif ($choice -eq "2") {
            Write-Log "$name 地址示例:"
            $examples | ForEach-Object { Write-Log "  $_" -Level INFO }
            $addr = Read-SingleLineSettingEditor -Prompt "$name 地址:" -InitialText $mirror_status
            if ($null -eq $addr) { Write-Log "已取消修改$name 地址"; return }
            if ($addr) {
                Remove-Item (Join-NormalizedPath $PSScriptRoot "disable_$file") -Force -ErrorAction SilentlyContinue
                Set-Content -Path (Join-NormalizedPath $PSScriptRoot $file) -Value $addr -Encoding UTF8
            }
            break
        }
        elseif ($choice -eq "3") {
            New-Item (Join-NormalizedPath $PSScriptRoot "disable_$file") -Force > $null
            Remove-Item (Join-NormalizedPath $PSScriptRoot $file) -Force -ErrorAction SilentlyContinue
            break
        }
        elseif ($choice -eq "4") { return }
    }
}


# 更新内核前缀设置
function Update-Core-Prefix {
    Write-Log "当前 Installer 内核路径前缀: $(Get-TextStatus "core_prefix.txt" "自动选择")"
    Write-Log "1. 使用自定义路径前缀 | 2. 自动选择路径前缀 | 3. 返回上一级"
    $choice = Get-UserInput
    if ($choice -eq "1") {
        $current_path = Get-TextStatus "core_prefix.txt" $null
        $path = Read-SingleLineSettingEditor -Prompt "Installer 内核路径前缀或绝对路径:" -InitialText $current_path
        if ($null -eq $path) { Write-Log "已取消修改 Installer 内核路径前缀"; return }
        if ($path) {
            if ([System.IO.Path]::IsPathRooted($path)) {
                $from = New-Object System.Uri($PSScriptRoot.Replace('\', '/') + '/')
                $path = $from.MakeRelativeUri((New-Object System.Uri($path.Replace('\', '/')))).ToString().Trim('/')
            }
            Set-Content -Path (Join-NormalizedPath $PSScriptRoot "core_prefix.txt") -Value $path -Encoding UTF8
        }
    }
    elseif ($choice -eq "2") { Remove-Item (Join-NormalizedPath $PSScriptRoot "core_prefix.txt") -Force -ErrorAction SilentlyContinue }
}


# 更新 Hotpatcher 端口
function Update-Hotpatcher-Port {
    Write-Log "当前 Hotpatcher 运行时通信端口: $(Get-TextStatus "hotpatcher_port.txt" "默认")"
    Write-Log "1. 配置自定义 | 2. 使用默认 | 3. 返回"
    $choice = Get-UserInput
    if ($choice -eq "1") {
        $current_port = Get-TextStatus "hotpatcher_port.txt" $null
        $port = Read-SingleLineSettingEditor -Prompt "Hotpatcher 运行时通信端口 (1..65535):" -InitialText $current_port
        if ($null -eq $port) { Write-Log "已取消修改 Hotpatcher 运行时通信端口"; return }
        $port_value = 0
        if (([int]::TryParse($port, [ref]$port_value)) -and ($port_value -ge 1) -and ($port_value -le 65535)) {
            Set-Content -Path (Join-NormalizedPath $PSScriptRoot "hotpatcher_port.txt") -Value $port_value -Encoding UTF8
            Write-Log "Hotpatcher 运行时通信端口设置已保存"
        } else {
            Write-Log "端口范围应为 1..65535, 未保存无效端口: $port" -Level WARNING
        }
    }
    elseif ($choice -eq "2") { Remove-Item (Join-NormalizedPath $PSScriptRoot "hotpatcher_port.txt") -Force -ErrorAction SilentlyContinue }
}


# 多行文本编辑器
function Read-MultiLineEditor {
    param (
        [string]$Prompt,
        [string]$InitialText = ""
    )

    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
        Write-Host $Prompt -NoNewline
        return (Read-Host).Trim()
    }

    function Get-CharacterDisplayWidth {
        param ([char]$Character)

        $code = [int][char]$Character
        if ([char]::IsControl($Character)) { return 0 }
        if (($code -ge 0x1100 -and $code -le 0x115F) -or
            ($code -ge 0x2329 -and $code -le 0x232A) -or
            ($code -ge 0x2E80 -and $code -le 0xA4CF) -or
            ($code -ge 0xAC00 -and $code -le 0xD7A3) -or
            ($code -ge 0xF900 -and $code -le 0xFAFF) -or
            ($code -ge 0xFE10 -and $code -le 0xFE19) -or
            ($code -ge 0xFE30 -and $code -le 0xFE6F) -or
            ($code -ge 0xFF00 -and $code -le 0xFF60) -or
            ($code -ge 0xFFE0 -and $code -le 0xFFE6)) {
            return 2
        }
        return 1
    }

    function Get-TextDisplayWidth {
        param (
            [string]$Text,
            [int]$EndIndex = -1
        )

        if ([string]::IsNullOrEmpty($Text)) { return 0 }
        if ($EndIndex -lt 0 -or $EndIndex -gt $Text.Length) { $EndIndex = $Text.Length }
        $width = 0
        for ($i = 0; $i -lt $EndIndex; $i++) {
            $width += Get-CharacterDisplayWidth $Text[$i]
        }
        return $width
    }

    function Get-VisibleTextByDisplayColumn {
        param (
            [string]$Text,
            [int]$StartColumn,
            [int]$MaxWidth
        )

        if ([string]::IsNullOrEmpty($Text) -or $MaxWidth -le 0) { return "" }
        $column = 0
        $visible_width = 0
        $result = [System.Text.StringBuilder]::new()
        for ($i = 0; $i -lt $Text.Length; $i++) {
            $char_width = Get-CharacterDisplayWidth $Text[$i]
            $next_column = $column + $char_width
            if ($next_column -le $StartColumn) {
                $column = $next_column
                continue
            }
            if ($column -lt $StartColumn) {
                $column = $next_column
                continue
            }
            if ($visible_width + $char_width -gt $MaxWidth) {
                break
            }
            [void]$result.Append($Text[$i])
            $visible_width += $char_width
            $column = $next_column
        }
        return $result.ToString()
    }

    function Get-DisplayColumnBoundary {
        param (
            [string]$Text,
            [int]$Column
        )

        if ([string]::IsNullOrEmpty($Text) -or $Column -le 0) { return 0 }
        $current_column = 0
        for ($i = 0; $i -lt $Text.Length; $i++) {
            $next_column = $current_column + (Get-CharacterDisplayWidth $Text[$i])
            if ($Column -le $next_column) {
                if ($Column -le $current_column) { return $current_column }
                return $next_column
            }
            $current_column = $next_column
        }
        return $current_column
    }

    function Get-DisplayPaddedText {
        param (
            [string]$Text,
            [int]$Width
        )

        if ($Width -le 0) { return "" }
        $display_width = Get-TextDisplayWidth $Text
        if ($display_width -gt $Width) {
            $Text = Get-VisibleTextByDisplayColumn $Text 0 $Width
            $display_width = Get-TextDisplayWidth $Text
        }
        if ($display_width -lt $Width) {
            return $Text + (" " * ($Width - $display_width))
        }
        return $Text
    }

    $normalized_text = $InitialText.Replace([string][char]13 + [string][char]10, [string][char]10).Replace([string][char]13, [string][char]10)
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in [regex]::Split($normalized_text, [string][char]10)) {
        [void]$lines.Add($line)
    }
    if ($lines.Count -eq 0) { [void]$lines.Add("") }

    $cursor_row = $lines.Count - 1
    $cursor_col = $lines[$cursor_row].Length
    $view_row = 0
    $view_col = 0
    $editor_height = [Math]::Max(3, [Math]::Min(10, [Console]::WindowHeight - 5))

    [Console]::WriteLine($Prompt)
    for ($i = 0; $i -lt ($editor_height + 3); $i++) {
        [Console]::WriteLine()
    }
    $top_separator_row = [Math]::Max(0, [Console]::CursorTop - $editor_height - 3)
    $editor_top = $top_separator_row + 1
    $previous_frame_rows = $null

    while ($true) {
        $window_width = [Math]::Max(20, [Console]::WindowWidth)
        $render_height = [Math]::Max(1, [Math]::Min($editor_height, [Console]::WindowHeight - $editor_top - 3))
        $text_width = [Math]::Max(1, $window_width - 3)
        $separator = "-" * ($window_width - 1)

        $cursor_display_col = Get-TextDisplayWidth $lines[$cursor_row] $cursor_col
        if ($cursor_row -lt $view_row) { $view_row = $cursor_row }
        if ($cursor_row -ge ($view_row + $render_height)) { $view_row = $cursor_row - $render_height + 1 }
        if ($cursor_display_col -lt $view_col) { $view_col = $cursor_display_col }
        if ($cursor_display_col -gt ($view_col + $text_width)) { $view_col = $cursor_display_col - $text_width }
        $view_col = Get-DisplayColumnBoundary $lines[$cursor_row] $view_col

        $frame_rows = [System.Collections.Generic.List[string]]::new()
        [void]$frame_rows.Add($separator)
        for ($i = 0; $i -lt $render_height; $i++) {
            $line_index = $view_row + $i
            if ($line_index -lt $lines.Count) {
                $marker = if ($line_index -eq $cursor_row) { "> " } else { "  " }
                $line_text = $lines[$line_index]
                $visible_text = Get-VisibleTextByDisplayColumn $line_text $view_col $text_width
                $row_text = $marker + $visible_text
            } else {
                $row_text = ""
            }
            [void]$frame_rows.Add((Get-DisplayPaddedText $row_text ($window_width - 1)))
        }

        $status_row = $editor_top + $render_height
        $help_row = $status_row + 2
        $cursor_status = "行 $($cursor_row + 1)/$($lines.Count) | 字符 $($cursor_col + 1)"
        $shortcut_help = "F10/Ctrl+O 保存 | Esc 取消 | Enter 换行 | Ctrl+U 清空 | PageUp/PageDown 翻页 | Home/End/方向键移动"
        $cursor_status = Get-VisibleTextByDisplayColumn $cursor_status 0 ($window_width - 1)
        $shortcut_help = Get-VisibleTextByDisplayColumn $shortcut_help 0 ($window_width - 1)
        [void]$frame_rows.Add($separator)
        [void]$frame_rows.Add((Get-DisplayPaddedText $cursor_status ($window_width - 1)))
        [void]$frame_rows.Add((Get-DisplayPaddedText $shortcut_help ($window_width - 1)))

        $cursor_screen_col = [Math]::Min($window_width - 1, 2 + ($cursor_display_col - $view_col))
        $cursor_screen_row = $editor_top + ($cursor_row - $view_row)
        try { [Console]::CursorVisible = $false } catch {}
        for ($i = 0; $i -lt $frame_rows.Count; $i++) {
            if (($null -eq $previous_frame_rows) -or ($previous_frame_rows.Count -le $i) -or ($previous_frame_rows[$i] -ne $frame_rows[$i])) {
                [Console]::SetCursorPosition(0, $top_separator_row + $i)
                [Console]::Write($frame_rows[$i])
            }
        }
        if (($null -ne $previous_frame_rows) -and ($previous_frame_rows.Count -gt $frame_rows.Count)) {
            for ($i = $frame_rows.Count; $i -lt $previous_frame_rows.Count; $i++) {
                [Console]::SetCursorPosition(0, $top_separator_row + $i)
                [Console]::Write(" " * ($window_width - 1))
            }
        }
        $previous_frame_rows = [System.Collections.Generic.List[string]]::new()
        foreach ($frame_row in $frame_rows) {
            [void]$previous_frame_rows.Add($frame_row)
        }
        [Console]::SetCursorPosition($cursor_screen_col, $cursor_screen_row)
        try { [Console]::CursorVisible = $true } catch {}

        $key = [Console]::ReadKey($true)
        if ($key.Key -eq [ConsoleKey]::F10) {
            try { [Console]::CursorVisible = $true } catch {}
            [Console]::SetCursorPosition(0, $help_row)
            [Console]::WriteLine()
            return ([string]::Join([Environment]::NewLine, $lines)).Trim()
        }
        if ((($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0) -and ($key.Key -eq [ConsoleKey]::O)) {
            try { [Console]::CursorVisible = $true } catch {}
            [Console]::SetCursorPosition(0, $help_row)
            [Console]::WriteLine()
            return ([string]::Join([Environment]::NewLine, $lines)).Trim()
        }
        if ((($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0) -and ($key.Key -eq [ConsoleKey]::U)) {
            $lines.Clear()
            [void]$lines.Add("")
            $cursor_row = 0
            $cursor_col = 0
            $view_row = 0
            $view_col = 0
            continue
        }
        if ((($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0) -and ($key.Key -eq [ConsoleKey]::A)) {
            $cursor_col = 0
            continue
        }
        if ((($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0) -and ($key.Key -eq [ConsoleKey]::E)) {
            $cursor_col = $lines[$cursor_row].Length
            continue
        }

        switch ($key.Key) {
            ([ConsoleKey]::Enter) {
                $line = $lines[$cursor_row]
                $before = $line.Substring(0, $cursor_col)
                $after = $line.Substring($cursor_col)
                $lines[$cursor_row] = $before
                $lines.Insert($cursor_row + 1, $after)
                $cursor_row++
                $cursor_col = 0
            }
            ([ConsoleKey]::Escape) {
                try { [Console]::CursorVisible = $true } catch {}
                [Console]::SetCursorPosition(0, $help_row)
                [Console]::WriteLine()
                return $null
            }
            ([ConsoleKey]::LeftArrow) {
                if ($cursor_col -gt 0) {
                    $cursor_col--
                } elseif ($cursor_row -gt 0) {
                    $cursor_row--
                    $cursor_col = $lines[$cursor_row].Length
                }
            }
            ([ConsoleKey]::RightArrow) {
                if ($cursor_col -lt $lines[$cursor_row].Length) {
                    $cursor_col++
                } elseif ($cursor_row -lt ($lines.Count - 1)) {
                    $cursor_row++
                    $cursor_col = 0
                }
            }
            ([ConsoleKey]::UpArrow) {
                if ($cursor_row -gt 0) {
                    $cursor_row--
                    $cursor_col = [Math]::Min($cursor_col, $lines[$cursor_row].Length)
                }
            }
            ([ConsoleKey]::DownArrow) {
                if ($cursor_row -lt ($lines.Count - 1)) {
                    $cursor_row++
                    $cursor_col = [Math]::Min($cursor_col, $lines[$cursor_row].Length)
                }
            }
            ([ConsoleKey]::PageUp) {
                $page_size = [Math]::Max(1, $render_height)
                $cursor_row = [Math]::Max(0, $cursor_row - $page_size)
                $cursor_col = [Math]::Min($cursor_col, $lines[$cursor_row].Length)
                $view_row = [Math]::Max(0, $view_row - $page_size)
            }
            ([ConsoleKey]::PageDown) {
                $page_size = [Math]::Max(1, $render_height)
                $cursor_row = [Math]::Min($lines.Count - 1, $cursor_row + $page_size)
                $cursor_col = [Math]::Min($cursor_col, $lines[$cursor_row].Length)
                $max_view_row = [Math]::Max(0, $lines.Count - $render_height)
                $view_row = [Math]::Min($max_view_row, $view_row + $page_size)
            }
            ([ConsoleKey]::Home) { $cursor_col = 0 }
            ([ConsoleKey]::End) { $cursor_col = $lines[$cursor_row].Length }
            ([ConsoleKey]::Backspace) {
                if ($cursor_col -gt 0) {
                    $line = $lines[$cursor_row]
                    $lines[$cursor_row] = $line.Remove($cursor_col - 1, 1)
                    $cursor_col--
                } elseif ($cursor_row -gt 0) {
                    $previous_length = $lines[$cursor_row - 1].Length
                    $lines[$cursor_row - 1] = $lines[$cursor_row - 1] + $lines[$cursor_row]
                    $lines.RemoveAt($cursor_row)
                    $cursor_row--
                    $cursor_col = $previous_length
                }
            }
            ([ConsoleKey]::Delete) {
                if ($cursor_col -lt $lines[$cursor_row].Length) {
                    $line = $lines[$cursor_row]
                    $lines[$cursor_row] = $line.Remove($cursor_col, 1)
                } elseif ($cursor_row -lt ($lines.Count - 1)) {
                    $lines[$cursor_row] = $lines[$cursor_row] + $lines[$cursor_row + 1]
                    $lines.RemoveAt($cursor_row + 1)
                }
            }
            default {
                if (-not [char]::IsControl($key.KeyChar)) {
                    $line = $lines[$cursor_row]
                    $lines[$cursor_row] = $line.Insert($cursor_col, $key.KeyChar)
                    $cursor_col++
                }
            }
        }
    }
}


# 单行文本设置编辑器
function Read-SingleLineSettingEditor {
    param (
        [string]$Prompt,
        [string]$InitialText = ""
    )

    $value = Read-MultiLineEditor -Prompt $Prompt -InitialText $InitialText
    if ($null -eq $value) { return $null }

    $normalized_value = $value.Replace([string][char]13 + [string][char]10, [string][char]10).Replace([string][char]13, [string][char]10)
    $line_values = @(
        [regex]::Split($normalized_value, [string][char]10) |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    if ($line_values.Count -gt 1) {
        Write-Log "该设置只支持单行文本, 已使用第一行内容" -Level WARNING
    }
    if ($line_values.Count -eq 0) { return "" }
    return $line_values[0]
}


# 更新启动参数
function Update-LaunchArgs {
    $path = Join-NormalizedPath $PSScriptRoot "launch_args.txt"
    $current_args = Get-TrimmedTextFile $path -Encoding UTF8
    Write-Log "编辑应用启动参数: F10/Ctrl+O 保存, Esc 取消, Enter 换行, Ctrl+U 清空, PageUp/PageDown 翻页, Home/End/方向键移动"
    $launch_args = Read-MultiLineEditor -Prompt "启动参数:" -InitialText $current_args
    if ($null -eq $launch_args) {
        Write-Log "已取消修改应用启动参数"
        return
    }
    if (-not [string]::IsNullOrWhiteSpace($launch_args)) {
        Write-FileWithStreamWriter -Path $path -Value $launch_args -Encoding UTF8
        Write-Log "应用启动参数已保存"
    } else {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        Write-Log "应用启动参数已清空"
    }
}


# 打开 Hotpatcher 配置管理 GUI
function Open-Hotpatcher-Gui {
    $config_path = Join-NormalizedPath $PSScriptRoot "patcher_config.json"
    if (!(Test-Path $config_path)) {
        Write-Log "未找到 Hotpatcher 默认配置文件, 正在导出默认配置到 $config_path"
        & python -m sd_webui_all_in_one self-manager patcher export-config
        $exit_code = Get-NativeCommandExitCode -Success $?
        if ($exit_code -ne 0) {
            Write-Log "导出 Hotpatcher 默认配置失败" -Level ERROR
            return
        }
    }

    Write-Log "正在打开 Hotpatcher 补丁系统 GUI: $config_path"
    & python -m sd_webui_all_in_one self-manager patcher gui --config "$config_path"
    $exit_code = Get-NativeCommandExitCode -Success $?
    if ($exit_code -ne 0) {
        Write-Log "Hotpatcher 补丁系统 GUI 退出异常: $exit_code" -Level WARNING
    }
}


# 获取用户输入
function Get-UserInput {
    return (Read-Host "==================================>").Trim()
}


function Main {
    Get-HelpMessage
    Get-Version
    Set-CorePrefix
    Initialize-EnvPath
    Test-PythonAndGit
    Set-Proxy -Legacy

    while ($true) {
        Write-Log "=== ComfyUI 本地设置菜单 ==="
        $menu = @(
            @{ id=0;  n="自动选择下载镜像源"; v=$(Get-ToggleStatus "disable_auto_mirror.txt" "启用" "禁用" $true) },
            @{ id=1;  n="网络代理设置"; v=$(if (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_proxy.txt")) { "禁用" } else { $proxy_status = Get-TextStatus "proxy.txt" $null; if (-not [string]::IsNullOrWhiteSpace($proxy_status)) { "手动设置 (地址: $proxy_status)" } else { "使用系统代理" } }) },
            @{ id=2;  n="Python 包管理器"; v=$(Get-ToggleStatus "disable_uv.txt" "Pip" "uv") },
            @{ id=3;  n="Hugging Face 下载镜像源"; v=$(Get-ToggleStatus "disable_hf_mirror.txt" "启用" "禁用" $true) },
            @{ id=4;  n="GitHub 下载镜像源"; v=$(Get-ToggleStatus "disable_gh_mirror.txt" "启用" "禁用" $true) },
            @{ id=5;  n="自动检查 Installer 更新"; v=$(Get-ToggleStatus "disable_update.txt" "启用" "禁用" $true) },
            @{ id=6;  n="模型下载来源"; v=$(Get-ToggleStatus "disable_model_mirror.txt" "ModelScope" "Hugging Face" $true) },
            @{ id=7;  n="应用启动参数"; v=$(Get-TextStatus "launch_args.txt") },
            @{ id=8;  n="创建启动快捷方式"; v=$(Get-ToggleStatus "enable_shortcut.txt" "启用" "禁用") },
            @{ id=9;  n="PyPI 软件包镜像"; v=$(Get-ToggleStatus "disable_pypi_mirror.txt" "启用" "禁用" $true) },
            @{ id=10; n="PyTorch CUDA 内存分配优化"; v=$(Get-ToggleStatus "disable_set_pytorch_cuda_memory_alloc.txt" "启用" "禁用" $true) },
            @{ id=11; n="启动前环境检测"; v=$(Get-ToggleStatus "disable_check_env.txt" "启用" "禁用" $true) },
            @{ id=12; n="Installer 内核路径前缀"; v=$(Get-TextStatus "core_prefix.txt" "自动") },
            @{ id=13; n="Hotpatcher 补丁系统"; v=$(Get-ToggleStatus "disable_hotpatcher.txt" "启用" "禁用" $true) },
            @{ id=14; n="Hotpatcher 运行时服务"; v=$(Get-ToggleStatus "enable_hotpatcher_runtime.txt" "启用" "禁用") },
            @{ id=15; n="Hotpatcher 运行时端口"; v=$(Get-TextStatus "hotpatcher_port.txt" "默认") },
            @{ id=16; n="自动快照"; v=$(Get-ToggleStatus "disable_snapshot.txt" "启用" "禁用" $true) },
            @{ id=17; n="打开 Hotpatcher 补丁系统 GUI" }
        )

        $menu | ForEach-Object {
            if ($_.ContainsKey("v")) {
                Write-Log "$($_.id). $($_.n): $($_.v)"
            } else {
                Write-Log "$($_.id). $($_.n)"
            }
        }
        Write-Log "18. 立即检查 Installer 更新 | 19. 打开在线文档 | 20. 退出设置"
        Write-Log "提示: 输入菜单编号后回车"

        $choice = Get-UserInput
        switch ($choice) {
            "0"  { Write-Log "提示: 启用「自动选择下载镜像源」时, PyPI、GitHub、Hugging Face、模型下载来源等手动镜像设置会被 Python CLI 自动覆盖; 如需手动调整这些设置, 请先禁用本项"; Set-ToggleSetting "disable_auto_mirror.txt" "自动选择下载镜像源" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_auto_mirror.txt")) }
            "1"  { Update-ProxySetting }
            "2"  { Set-ToggleSetting "disable_uv.txt" "Python 包管理器" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_uv.txt")) }
            "3"  { Update-Mirror-Setting "hf_mirror.txt" "Hugging Face 下载镜像源" @("https://hf-mirror.com", "https://huggingface.sukaka.top") }
            "4"  { Update-Mirror-Setting "gh_mirror.txt" "GitHub 下载镜像源" @("https://ghfast.top/https://github.com", "https://mirror.ghproxy.com/https://github.com") }
            "5"  { Set-ToggleSetting "disable_update.txt" "自动检查 Installer 更新" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_update.txt")) }
            "6"  { Set-ToggleSetting "disable_model_mirror.txt" "模型下载来源" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_model_mirror.txt")) }
            "7"  { Update-LaunchArgs }
            "8"  { Set-ToggleSetting "enable_shortcut.txt" "创建启动快捷方式" (!(Test-Path (Join-NormalizedPath $PSScriptRoot "enable_shortcut.txt"))) }
            "9"  { Set-ToggleSetting "disable_pypi_mirror.txt" "PyPI 软件包镜像" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_pypi_mirror.txt")) }
            "10" { Set-ToggleSetting "disable_set_pytorch_cuda_memory_alloc.txt" "PyTorch CUDA 内存分配优化" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_set_pytorch_cuda_memory_alloc.txt")) }
            "11" { Set-ToggleSetting "disable_check_env.txt" "启动前环境检测" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_check_env.txt")) }
            "12" { Update-Core-Prefix }
            "13" { Set-ToggleSetting "disable_hotpatcher.txt" "Hotpatcher 补丁系统" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_hotpatcher.txt")) }
            "14" { Set-ToggleSetting "enable_hotpatcher_runtime.txt" "Hotpatcher 运行时服务" (!(Test-Path (Join-NormalizedPath $PSScriptRoot "enable_hotpatcher_runtime.txt"))) }
            "15" { Update-Hotpatcher-Port }
            "16" { Set-ToggleSetting "disable_snapshot.txt" "自动快照" (Test-Path (Join-NormalizedPath $PSScriptRoot "disable_snapshot.txt")) }
            "17" { Open-Hotpatcher-Gui }
            "18" { Remove-Item (Join-NormalizedPath $PSScriptRoot "update_time.txt") -Force -ErrorAction SilentlyContinue; Update-Installer -DisableRestart }
            "19" { Start-Process "https://licyk.github.io/sd-webui-all-in-one/installer/comfyui/" }
            "20" { Write-Log "退出设置菜单"; return }
        }
    }
    if (!($script:NoPause)) { Read-Host | Out-Null }
}

###################

Main