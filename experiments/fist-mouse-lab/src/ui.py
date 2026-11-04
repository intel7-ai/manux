"""Camera, live gesture feedback and an owned mouse practice target."""
import tkinter as tk
import time
import cv2
import customtkinter as ctk
from PIL import Image
from .worker import RUNTIME
from .absolute_pointer import AbsolutePointer

BG='#101819'; PANEL='#192526'; INK='#edf4ee'; MUTED='#9eb2aa'; GREEN='#b1ef91'
CONNECTIONS=[(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),
             (9,13),(13,14),(14,15),(15,16),(13,17),(0,17),(17,18),(18,19),(19,20)]
MODES={'fist':'주먹 · 이동','scroll':'검지 · 휠','right':'새끼 · 우클릭','rest':'쉬는 중',
       'uncertain':'자세 확인','transition':'전환 중','waiting':'손 대기'}


class Window:
    def __init__(self,worker):
        self.worker=worker;self.serial=-1;self.closed=False;self.image=None;self.state={}
        self.last_paint=0.;self.pad_size=None
        self.received=dict(left=0,double=0,right=0,wheel=0)
        ctk.set_appearance_mode('dark')
        self.root=ctk.CTk();self.root.title('MANUX Fist Mouse Lab')
        self.root.geometry('1280x900+20+20');self.root.minsize(1100,800);self.root.configure(fg_color=BG)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.bind('<Escape>',lambda _:self.worker.command('native',False))
        self.root.grid_columnconfigure(0,weight=1);self.root.grid_rowconfigure(1,weight=1)
        self.build();self.root.after(16,self.tick)

    def label(self,parent,text,size=14,color=INK,**kw):
        return ctk.CTkLabel(parent,text=text,font=('맑은 고딕',size),text_color=color,**kw)
    def button(self,parent,text,command,primary=False,**kw):
        return ctk.CTkButton(parent,text=text,command=command,height=40,corner_radius=9,
            fg_color=GREEN if primary else '#2c403b',hover_color='#91c975' if primary else '#3a554d',
            text_color=BG if primary else INK,font=('맑은 고딕',13),**kw)

    def build(self):
        head=ctk.CTkFrame(self.root,fg_color='transparent');head.grid(row=0,column=0,sticky='ew',padx=24,pady=20)
        self.label(head,'MANUX',27,GREEN).pack(side='left')
        self.label(head,'주먹으로 움직이는 마우스',18).pack(side='left',padx=18)
        self.stop_button=self.button(head,'중지  Esc',lambda:self.worker.command('native',False),width=100)
        self.stop_button.pack(side='right')
        self.native_button=self.button(head,'실제 마우스 켜기',self.toggle,True,width=155)
        self.native_button.pack(side='right',padx=10)
        body=ctk.CTkFrame(self.root,fg_color='transparent');body.grid(row=1,column=0,sticky='nsew',padx=24)
        body.grid_columnconfigure(0,weight=3);body.grid_columnconfigure(1,weight=2)
        body.grid_rowconfigure(1,weight=1)
        self.mode=self.label(body,'카메라 준비 중',20,GREEN,anchor='w');self.mode.grid(row=0,column=0,sticky='ew',pady=(0,8))
        self.pad_title=self.label(body,'연습 패드 · 실제 커서는 움직이지 않습니다',15,anchor='w')
        self.pad_title.grid(row=0,column=1,sticky='ew',padx=(16,0),pady=(0,8))
        self.video_box=ctk.CTkFrame(body,fg_color='#080e0f',corner_radius=12)
        self.video_box.grid(row=1,column=0,sticky='nsew')
        self.video=ctk.CTkLabel(self.video_box,text='카메라를 연결하고 있습니다',text_color=MUTED)
        self.video.place(relx=.5,rely=.5,anchor='center')
        right=ctk.CTkFrame(body,fg_color=PANEL,corner_radius=12);right.grid(row=1,column=1,sticky='nsew',padx=(16,0))
        self.pad=tk.Canvas(right,bg=PANEL,highlightthickness=0,takefocus=True)
        self.pad.pack(fill='both',expand=True,padx=10,pady=10)
        for sequence,kind in (('<ButtonRelease-1>','left'),('<Double-Button-1>','double'),
                              ('<ButtonRelease-3>','right'),('<MouseWheel>','wheel')):
            self.pad.bind(sequence,lambda e,k=kind:self.receive(e,k))
        self.received_label=self.label(right,'패드가 받은 실제 입력  좌 0 · 더블 0 · 우 0 · 휠 0',11,MUTED)
        self.received_label.pack(pady=(0,10))
        self.hint=self.label(body,'주먹을 편하게 쥐고 엄지를 쉬는 위치에 두세요.',15,anchor='w',wraplength=720)
        self.hint.grid(row=2,column=0,sticky='ew',pady=(10,2))
        self.counts_label=self.label(body,'인식 횟수  좌 0 · 더블 0 · 우 0 · 휠 0',13,GREEN,anchor='w')
        self.counts_label.grid(row=2,column=1,sticky='ew',padx=(16,0),pady=(10,2))
        self.metrics=self.label(body,'',12,MUTED,anchor='w');self.metrics.grid(row=3,column=0,sticky='ew')
        self.event_label=self.label(body,'명령을 실행하면 여기에 표시됩니다',12,MUTED,anchor='w')
        self.event_label.grid(row=3,column=1,sticky='ew',padx=(16,0))
        bottom=ctk.CTkFrame(self.root,fg_color=PANEL,corner_radius=12)
        bottom.grid(row=2,column=0,sticky='ew',padx=24,pady=(16,20))
        bottom.grid_columnconfigure(0,weight=1);bottom.grid_columnconfigure(1,weight=1)
        mapping=ctk.CTkFrame(bottom,fg_color='transparent');mapping.grid(row=0,column=0,sticky='nsew',padx=18,pady=14)
        self.label(mapping,'01 주먹 이동   02 엄지 벌렸다 되돌리기 = 좌클릭',16).pack(anchor='w')
        self.label(mapping,'03 엄지 빠르게 두 번 = 더블클릭',16).pack(anchor='w',pady=3)
        self.label(mapping,'04 새끼 펴기 = 우클릭   05 검지 펴고 상하 = 휠',16).pack(anchor='w')
        self.label(mapping,'06 엄지 0.3초 유지 → 손 이동 = 드래그 · 되돌리면 놓기',14,GREEN).pack(anchor='w',pady=(4,0))
        self.label(mapping,'손을 펴거나 빼면 정지 · 눌린 버튼도 해제',12,MUTED).pack(anchor='w',pady=(5,0))
        controls=ctk.CTkFrame(bottom,fg_color='transparent');controls.grid(row=0,column=1,sticky='nsew',padx=18,pady=12)
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x')
        self.area_label=self.label(row,'조작 영역',12);self.area_label.pack(side='left')
        self.speed=ctk.CTkSlider(row,from_=.45,to=1.,number_of_steps=55,progress_color=GREEN,
                               command=lambda v:self.change('area_scale',v))
        self.speed.set(self.worker.settings['area_scale']);self.speed.pack(side='right',fill='x',expand=True,padx=(16,0))
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x',pady=(8,0))
        self.label(row,'떨림 보정',12).pack(side='left')
        stability=ctk.CTkSlider(row,from_=0,to=1,number_of_steps=20,progress_color=GREEN,
                              command=lambda v:self.change('stability',v))
        stability.set(self.worker.settings['stability']);stability.pack(side='right',fill='x',expand=True,padx=(16,0))
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x',pady=8)
        self.label(row,'휠 속도',12).pack(side='left')
        slider=ctk.CTkSlider(row,from_=.25,to=3,number_of_steps=22,progress_color=GREEN,
                           command=lambda v:self.change('scroll_speed',v))
        slider.set(self.worker.settings['scroll_speed']);slider.pack(side='right',fill='x',expand=True,padx=(16,0))
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x')
        self.calibrate=self.button(row,'내 엄지 맞추기 · 10초',lambda:self.worker.command('calibrate'),width=168)
        self.calibrate.pack(side='left')
        self.record=self.button(row,'이동 점검 · 20초',lambda:self.worker.command('record','movement'),True,width=145)
        self.record.pack(side='left',padx=8)
        self.button(row,'초기 위치',self.reset_pad,width=80).pack(side='left')
        row=ctk.CTkFrame(controls,fg_color='transparent');row.pack(fill='x',pady=(8,0))
        cfg=self.worker.camera_settings
        self.mirror=ctk.CTkSwitch(row,text='거울 화면',command=lambda:self.worker.command('camera',{'mirror':bool(self.mirror.get())}),
                                font=('맑은 고딕',12),progress_color='#527444')
        if cfg['mirror']:self.mirror.select()
        self.mirror.pack(side='left')
        self.rotation=ctk.CTkOptionMenu(row,values=['0°','90°','180°','270°'],width=85,
            command=lambda v:self.worker.command('camera',{'rotation':int(v[:-1])}),fg_color='#2c403b',button_color='#3a554d')
        self.rotation.set(str(cfg['rotation'])+'°');self.rotation.pack(side='right')
        self.label(row,'카메라 회전',12,MUTED).pack(side='right',padx=8)
        reach_row=ctk.CTkFrame(mapping,fg_color='transparent');reach_row.pack(fill='x',pady=(10,0))
        self.click_test=self.button(reach_row,'클릭·드래그 점검 · 25초',lambda:self.worker.command('record','clicks'),width=195)
        self.click_test.pack(side='left',padx=(0,8))
        self.cancel_button=self.button(reach_row,'등록 취소',lambda:self.worker.command('cancel'),width=85)
        self.cancel_button.pack(side='left')
        self.label(mapping,'카메라 중앙의 초록 영역 = 모니터 전체',11,GREEN).pack(anchor='w',pady=(6,0))
        self.test_hint=self.label(bottom,'연습 패드에서 먼저 확인하고, 실제 마우스 켜기를 누르세요.',14,GREEN,
                                  anchor='w',wraplength=1200,justify='left')
        self.test_hint.grid(row=1,column=0,columnspan=2,sticky='ew',padx=18,pady=(0,4))
        self.debug=self.label(bottom,'',11,MUTED,anchor='w')
        self.debug.grid(row=2,column=0,columnspan=2,sticky='ew',padx=18,pady=(0,12))

    def change(self,key,value):
        self.worker.command('settings',{key:round(value,2)})
    def toggle(self):self.worker.command('native',not self.state.get('native_output',False))
    def reset_pad(self):
        self.worker.command('reset');self.received=dict(left=0,double=0,right=0,wheel=0)
    def receive(self,event,kind):
        self.received[kind]+=int(event.delta/120) if kind=='wheel' else 1
        self.received_label.configure(text=f"패드가 받은 실제 입력  좌 {self.received['left']} · 더블 {self.received['double']} · 우 {self.received['right']} · 휠 {self.received['wheel']}")

    def draw_pad(self,state):
        canvas=self.pad
        w,h=max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        if self.pad_size==(w,h):
            self.draw_pointer(state);return
        self.pad_size=(w,h);canvas.delete('all')
        canvas.create_rectangle(2,2,w-2,h-2,outline='#49634f',width=2)
        for x,y,title,sub in ((.26,.29,'좌클릭','엄지를 벌렸다 되돌리기'),(.74,.29,'더블클릭','엄지 두 번 빠르게'),
                              (.26,.71,'우클릭','새끼 펴기'),(.74,.71,'휠','검지 펴고 위아래')):
            radius=min(w*.20,h*.18)
            canvas.create_oval(x*w-radius,y*h-radius,x*w+radius,y*h+radius,fill='#243b32',outline='#49634f',width=2)
            canvas.create_text(x*w,y*h-8,text=title,fill=INK,font=('맑은 고딕',17,'bold'))
            canvas.create_text(x*w,y*h+22,text=sub,fill=MUTED,font=('맑은 고딕',10))
        self.pad_dot=canvas.create_oval(0,0,0,0,fill=GREEN,outline=INK,width=2,tags='pointer')
        self.pad_h=canvas.create_line(0,0,0,0,fill=INK,tags='pointer')
        self.pad_v=canvas.create_line(0,0,0,0,fill=INK,tags='pointer')
        self.draw_pointer(state)

    def draw_pointer(self,state):
        canvas=self.pad;w,h=self.pad_size
        canvas.itemconfigure('pointer',state='hidden' if state.get('native_output') else 'normal')
        x,y=self.worker.practice_pointer.cursor();x=x/1000*w;y=y/600*h
        canvas.coords(self.pad_dot,x-8,y-8,x+8,y+8)
        canvas.coords(self.pad_h,x-14,y,x+14,y)
        canvas.coords(self.pad_v,x,y-14,x,y+14)

    def tick(self):
        if self.closed:return
        self.worker.heartbeat()
        serial,frame,state=self.worker.snapshot()
        # Only the three pointer items repaint at ~60 Hz; camera/text stay 15 Hz.
        self.draw_pad(state)
        # Camera inference keeps running independently; preview painting at 15Hz
        # avoids repeatedly rescaling large images while the desktop is in use.
        if serial!=self.serial and time.perf_counter()-self.last_paint>=1/15:
            self.last_paint=time.perf_counter()
            self.serial=serial;self.state=state
            active=state.get('native_output',False)
            self.native_button.configure(text='연습 모드로 돌아가기' if active else '실제 마우스 켜기',state='disabled' if state.get('calibrating') else 'normal')
            self.mode.configure(text='엄지 유지 · 드래그' if state.get('dragging') else
                                '드래그 준비 · 손을 움직이세요' if state.get('drag_ready') else
                                '엄지 되돌리기 · 클릭 대기' if state.get('pending_click') else MODES.get(state.get('mode'),'연결 준비 중'))
            self.pad_title.configure(text='실제 마우스 · 이 패드에 클릭해보세요' if active else '연습 패드 · 실제 커서는 움직이지 않습니다')
            self.hint.configure(text=state.get('error') or state.get('camera_error') or state.get('reason','카메라 준비 중'))
            c=state.get('counts',{})
            self.counts_label.configure(text=f"인식 횟수  좌 {c.get('left',0)} · 더블 {c.get('double',0)} · 우 {c.get('right',0)} · 드래그 {c.get('drag',0)}")
            self.metrics.configure(text=f"{state.get('fps',0):.0f} FPS  ·  수신→판정 {state.get('age_ms',0):.0f}ms  ·  초록 경계 = 화면 끝")
            e=state.get('events',[])
            self.event_label.configure(text='  /  '.join(x['text'] for x in e[-2:]) or '명령을 실행하면 여기에 표시됩니다')
            f=state.get('features')
            self.debug.configure(text=(f"엄지 {f['thumb']:.2f} / 클릭 기준 {state['settings']['thumb_enter']:.2f}   ·   클릭 {'준비됨' if state.get('armed') else '대기'}   ·   검지·중지·약지·새끼 펼침 " + ' / '.join(f'{v:.2f}' for v in f['fingers'])) if f else '손 전체가 보이게 해주세요 · 추적이 끊기면 명령을 중지합니다')
            if state.get('recording'):
                hint=f"{state.get('test_elapsed',0):.0f}/{state.get('test_duration',20)}초   {state.get('test_step','')}"
            elif state.get('message'):hint=state['message']
            elif state.get('report'):hint='점검 기록 저장 완료 · 관절 좌표와 실행한 명령을 저장했습니다'
            else:hint='초록 영역의 경계가 모니터 끝입니다 · 조작 영역이 작을수록 적게 움직여도 도달합니다 · Esc 중지'
            self.test_hint.configure(text=hint)
            busy=state.get('calibrating') or state.get('recording')
            self.record.configure(state='disabled' if busy else 'normal')
            self.click_test.configure(state='disabled' if busy else 'normal')
            self.calibrate.configure(state='disabled' if busy else 'normal')
            self.cancel_button.configure(state='normal' if state.get('calibrating') else 'disabled')
            area_scale=state.get('settings',{}).get('area_scale')
            if area_scale is not None and abs(self.speed.get()-area_scale)>.011:self.speed.set(area_scale)
            if area_scale is not None:self.area_label.configure(text=f'조작 영역 {area_scale:.0%}')
            if frame is not None:
                view=frame.copy();vh,vw=view.shape[:2];points=state.get('points')
                area=AbsolutePointer.area(state.get('settings',{}).get('area_scale',1.),state.get('settings',{}).get('area_base'))
                left,top,right,bottom=(round(area[0]*vw),round(area[1]*vh),round(area[2]*vw)-1,round(area[3]*vh)-1)
                inside=view[max(0,top):bottom+1,max(0,left):right+1].copy()
                view=(view*.45).astype('uint8')
                view[max(0,top):bottom+1,max(0,left):right+1]=inside
                cv2.rectangle(view,(left,top),(right,bottom),(145,239,177),2,cv2.LINE_AA)
                cv2.line(view,((left+right)//2,top),((left+right)//2,bottom),(70,100,80),1)
                cv2.line(view,(left,(top+bottom)//2),(right,(top+bottom)//2),(70,100,80),1)
                if points:
                    xy=[(round(p[0]*vw),round(p[1]*vh)) for p in points]
                    for a,b in CONNECTIONS:cv2.line(view,xy[a],xy[b],(99,156,110),2,cv2.LINE_AA)
                    for i,p in enumerate(xy):cv2.circle(view,p,5 if i in (4,8,20) else 3,(80,201,246) if i in (4,8,20) else (180,226,190),-1,cv2.LINE_AA)
                anchor=state.get('anchor')
                if anchor:
                    p=(round(anchor[0]*vw),round(anchor[1]/(state['height']/state['width'])*vh))
                    cv2.circle(view,p,12,(145,239,177),3,cv2.LINE_AA)
                scale=min(max(20,self.video_box.winfo_width()-12)/vw,max(20,self.video_box.winfo_height()-12)/vh)
                size=(max(1,round(vw*scale)),max(1,round(vh*scale)))
                self.image=ctk.CTkImage(Image.fromarray(cv2.cvtColor(view,cv2.COLOR_BGR2RGB)),size=size)
                self.video.configure(image=self.image,text='')
        if (RUNTIME/'show.request').exists():
            (RUNTIME/'show.request').unlink(missing_ok=True);self.root.deiconify();self.root.lift()
        if self.worker.stop_event.is_set():self.close();return
        self.root.after(16,self.tick)

    def close(self):
        if self.closed:return
        self.closed=True;self.worker.close();self.root.destroy()
    def run(self):self.root.mainloop()
