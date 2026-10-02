# Remote PowerShell Commander

ระบบควบคุม Windows จากระยะไกลผ่าน Claude AI

## วิธีใช้งาน (ไม่ต้องส่งไฟล์!)

### เครื่องที่จะถูกควบคุม (HOST)
เปิด PowerShell แล้วพิมพ์คำสั่งนี้:
```powershell
iex (irm 'https://raw.githubusercontent.com/Knightlost/remote-shell/main/bootstrap_host.ps1')
```

### เครื่องที่จะควบคุม (CONTROLLER)
เปิด PowerShell แล้วพิมพ์คำสั่งนี้:
```powershell
iex (irm 'https://raw.githubusercontent.com/Knightlost/remote-shell/main/bootstrap_controller.ps1')
```

## ขั้นตอน
1. เปิด PowerShell บนเครื่องที่จะถูกควบคุม → วางคำสั่ง HOST
2. รอจนได้ **Remote ID** และ **Passcode**
3. เปิด PowerShell บนเครื่องที่จะควบคุม → วางคำสั่ง CONTROLLER
4. กรอก Remote ID และ Passcode
5. เลือก [1] Chat with Claude → ควบคุมผ่าน Claude ได้เลย
