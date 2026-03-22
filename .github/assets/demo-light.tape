Output .github/assets/demo-light.mp4
Output .github/assets/demo-light.gif

Set Theme "rose-pine-dawn"
Set Shell "bash"
Set FontSize 18
Set Width 1920
Set Height 1080
Set Padding 0

Hide
Type "pixi shell" Enter
Sleep 5s
Type "clear" Enter
Sleep 1s
Type "pixi-browse"
Enter
Sleep 3s
Show

Sleep 3s
Type@250ms "/py-rattler"
Sleep 3s
Enter
Sleep 3s

Down@250ms 4
Sleep 1s
Enter
Sleep 3s
# scroll down a bit
Down@250ms 5
Sleep 2s
# go to files pane
Tab
Sleep 1s
Tab
Sleep 2s
# go down
Down 20
Sleep 2s
# preview file
Enter
Sleep 2s
Enter
Sleep 3s
Escape
Sleep 1s
# go to dependencies view
Shift+Tab
# cycle through dependencies/constraints/run exports
Sleep 1
Type "]"
Sleep 1
Type "]"
Sleep 1
Type "]"
# Open matchspec
Sleep 1
Enter
Sleep 3
Enter
Sleep 3s

Type "c"
Sleep 1s
Backspace 11
Type@250ms "bioconda" Enter
Sleep 3s

Down@500ms 5 Enter
Sleep 1s
Down@500ms 2
Sleep 5s
