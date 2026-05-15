from django.urls import path
from . import views

urlpatterns = [
    path('conversations/', views.ConversationListCreateView.as_view(), name='conversation-list'),
    path('conversations/<uuid:conversation_id>/messages/', views.MessageListView.as_view(), name='message-list'),
    
    path('blocks/', views.BlockedUsersListView.as_view(), name='blocked-list'),
    path('block/', views.BlockUserView.as_view(), name='block-user'),
    path('unblock/<uuid:user_id>/', views.UnblockUserView.as_view(), name='unblock-user'),
    
    path('report/', views.ReportUserView.as_view(), name='report-user'),
    path('upload-image/', views.ImageUploadView.as_view(), name='upload-image'),
]
